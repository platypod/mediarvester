"""OpenTelemetry setup: metrics + traces via plain OTLP, exported to whatever
`OTEL_EXPORTER_OTLP_ENDPOINT` points at -- no Grafana/Mimir-specific code
anywhere in this module. Every exporter reads its target from the standard
`OTEL_EXPORTER_OTLP_*` / `OTEL_SERVICE_NAME` / `OTEL_RESOURCE_ATTRIBUTES` env
vars, so an unset environment (local dev, a fresh clone) just gets exporters
that retry quietly in the background rather than crashing startup.

Profiling is the one deliberate exception: OTel's own profiling signal is
still alpha (2026-03) with no usable Python SDK, so this uses Pyroscope's own
client instead -- not OTLP, an explicit, acknowledged stopgap. `pyroscope.otel`
still ties profile samples back to the trace/span active on the same thread,
so the two signals correlate despite the different wire protocol.

Call order matters:
  1. `configure()` -- as early as possible, before anything else in this
     package imports `db` (SQLAlchemy instrumentation wraps
     `create_async_engine` itself, so it must be active before `db.py`'s
     module-level `create_async_engine(...)` call runs).
  2. `instrument_app(app)` -- once the FastAPI app object exists.
"""

from __future__ import annotations

import functools
from logging import getLogger
from os import environ
from typing import Callable, TypeVar

from opentelemetry import context as otel_context
from opentelemetry import metrics, trace
from opentelemetry.metrics import Observation
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = getLogger(__name__)

_T = TypeVar("_T")

_configured = False

# Placeholders, reassigned inside configure() -- see the note there for why
# `get_tracer`/`get_meter` can't just run here at module import time.
tracer = trace.get_tracer("mediarvester")
meter = metrics.get_meter("mediarvester")


def configure() -> None:
    """Idempotent -- safe to call more than once (tests, reload).

    `trace.get_tracer(...)`/`metrics.get_meter(...)` bind to whichever
    TracerProvider/MeterProvider is globally active *at the moment they're
    called* -- calling `set_tracer_provider`/`set_meter_provider` afterwards
    does not retroactively upgrade an already-obtained Tracer/Meter. Since
    other modules do `from services.telemetry import tracer, meter` at
    their own import time, and this module is deliberately imported (see
    main.py) before those, `tracer`/`meter` must be reassigned here, after
    the real providers are installed below -- not just once at module
    scope, which would permanently bind them to the SDK's default no-op
    implementations.
    """
    global _configured, tracer, meter
    if _configured:
        return
    _configured = True

    resource = Resource.create(
        {
            "service.name": environ.get("OTEL_SERVICE_NAME", "mediarvester"),
            "service.namespace": "media",
            # Already baked into the image (see Dockerfile) -- reused rather
            # than introducing a second version knob.
            "service.version": environ.get("VERSION", "dev"),
        }
    )

    # OTLPSpanExporter()/OTLPMetricExporter() silently default to
    # localhost:4317 when OTEL_EXPORTER_OTLP_ENDPOINT is unset -- confirmed
    # empirically this is NOT quiet: they actively retry against that
    # default and log WARNING/ERROR lines on every export cycle forever.
    # Only wire up the exporters (and pay that cost) when a real endpoint
    # is actually configured; otherwise the provider has zero processors/
    # readers and every span/metric call is a true no-op -- no network
    # activity, no log noise, for local dev / a fresh clone.
    endpoint_configured = bool(environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))

    tracer_provider = TracerProvider(resource=resource)
    if endpoint_configured:
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    if environ.get("PYROSCOPE_SERVER_ADDRESS"):
        # Deferred import: keeps `pyroscope-io`/`pyroscope-otel` optional for
        # anyone running without profiling configured (e.g. a bare `pip
        # install` for local dev without the full stack).
        from pyroscope.otel import PyroscopeSpanProcessor

        tracer_provider.add_span_processor(PyroscopeSpanProcessor())
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=(
            [PeriodicExportingMetricReader(OTLPMetricExporter())] if endpoint_configured else []
        ),
    )
    metrics.set_meter_provider(meter_provider)

    tracer = trace.get_tracer("mediarvester")
    meter = metrics.get_meter("mediarvester")

    SQLAlchemyInstrumentor().instrument()
    LoggingInstrumentor().instrument()
    _configure_profiling()

    logger.info(
        "telemetry configured: otlp_endpoint=%s profiling=%s",
        environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "(unset -- exporters idle)",
        "enabled" if environ.get("PYROSCOPE_SERVER_ADDRESS") else "disabled",
    )


def _configure_profiling() -> None:
    server_address = environ.get("PYROSCOPE_SERVER_ADDRESS")
    if not server_address:
        return
    import pyroscope

    pyroscope.configure(
        application_name=environ.get("OTEL_SERVICE_NAME", "mediarvester"),
        server_address=server_address,
    )


def instrument_app(app) -> None:
    """Call once the FastAPI app object exists (main.py, after `app =
    FastAPI(...)`). Excludes the static/SPA mounts -- they're noise, not
    meaningful application spans, and the catch-all route would otherwise
    show up as one very generic route template regardless of what was
    actually requested."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="media-files,assets")


def propagate_context(fn: Callable[..., _T]) -> Callable[..., _T]:
    """Wrap a callable that's about to cross into a `ThreadPoolExecutor`
    (`Executor.submit`, and anything built on it such as
    `loop.run_in_executor`) so the OTel context active *at wrap time* --
    e.g. the span opened for an incoming request, or for a download's own
    unit of work -- is still active inside the callable, rather than the
    callable starting with an empty context and any spans/metrics it
    creates showing up disconnected from what triggered them.

    Verified empirically (bare `contextvars`, no OTel involved) that a
    plain `ThreadPoolExecutor.submit` does NOT propagate context on its
    own -- the worker thread gets a fresh, empty one. This is the one place
    that actually needs it.

    NOT needed for `asyncio.run_coroutine_threadsafe` (used by
    `Downloader._schedule`): also verified empirically that it already
    propagates the calling thread's context automatically, because it's
    built on `loop.call_soon_threadsafe`, whose `Handle` defaults to
    `contextvars.copy_context()` at schedule time. Wrapping that too would
    be redundant.
    """
    ctx = otel_context.get_current()

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> _T:
        token = otel_context.attach(ctx)
        try:
            return fn(*args, **kwargs)
        finally:
            otel_context.detach(token)

    return wrapper


_cached_gauge_values: dict[str, float] = {}


def create_cached_gauge(name: str, description: str = "") -> Callable[[float], None]:
    """Register an ObservableGauge backed by a plain cached value rather
    than a live callback. ObservableGauge callbacks must be synchronous, so
    anything needing an async DB query (e.g. the degraded-service check,
    a MediaItem count) can't compute its value inline here -- the caller is
    responsible for periodically calling the returned setter (see
    services/poller.py's `_refresh_gauges`) to keep the cached value fresh.
    """

    def _callback(options):
        if name in _cached_gauge_values:
            yield Observation(_cached_gauge_values[name])

    meter.create_observable_gauge(name, callbacks=[_callback], description=description)

    def setter(value: float) -> None:
        _cached_gauge_values[name] = value

    return setter
