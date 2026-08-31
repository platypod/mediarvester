async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    credentials: 'include',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (res.redirected) {
    // Session expired mid-request: Authelia forward-auth intercepted and
    // fetch() silently followed the redirect to its login page. Hand off to
    // a real navigation so the browser shows the login UI and Authelia's
    // `rd` param can bring the user back here after they log in.
    window.location.href = res.url
    return new Promise<T>(() => {})
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${method} ${url} → ${res.status}${text ? ': ' + text : ''}`)
  }
  if (res.status === 204) return null as T
  return res.json()
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body: unknown) => request<T>('POST', url, body),
  patch: <T>(url: string, body: unknown) => request<T>('PATCH', url, body),
  delete: (url: string) => request<void>('DELETE', url),
}
