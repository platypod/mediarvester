<a id="readme-top"></a>


<!-- PROJECT SHIELDS -->
[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![MIT License][license-shield]][license-url]



<!-- PROJECT LOGO -->
<br />
<div align="center">
  <a href="https://github.com/platypod/mediarvester">
    <img src="https://github.com/platypod/stack/raw/main/doc/images/logo.png" alt="Logo" width="80" height="80">
  </a>

<h3 align="center">mediarvester</h3>

  <p align="center">
    A self-hosted media downloader supporting YouTube, Twitter, Instagram,
    and any platform <a href="https://github.com/yt-dlp/yt-dlp">yt-dlp</a> handles.
    Built with FastAPI and served as a web application.
    <br />
    <a href="https://github.com/platypod/mediarvester/issues/new?labels=bug&template=bug-report---.md">Report Bug</a>
    ·
    <a href="https://github.com/platypod/mediarvester/issues/new?labels=enhancement&template=feature-request---.md">Request Feature</a>
  </p>
</div>



<!-- TABLE OF CONTENTS -->
<details>
  <summary>Table of Contents</summary>
  <ol>
    <li><a href="#about-the-project">About The Project</a></li>
    <li>
      <a href="#getting-started">Getting Started</a>
      <ul>
        <li><a href="#prerequisites">Prerequisites</a></li>
        <li><a href="#build">Build</a></li>
        <li><a href="#run-locally">Run locally</a></li>
      </ul>
    </li>
    <li><a href="#usage">Usage</a></li>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#license">License</a></li>
    <li><a href="#acknowledgments">Acknowledgments</a></li>
  </ol>
</details>



<!-- ABOUT THE PROJECT -->
## About The Project

mediarvester is a self-hosted FastAPI web application that wraps
[yt-dlp](https://github.com/yt-dlp/yt-dlp) to download media from
YouTube, Twitter, Instagram, and any other supported platform.
It supports cookie-based authentication for age-restricted or login-gated content.

Images are published to [ghcr.io/platypod/mediarvester](https://ghcr.io/platypod/mediarvester).

<p align="right">(<a href="#readme-top">back to top</a>)</p>



<!-- GETTING STARTED -->
## Getting Started

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with [buildx](https://docs.docker.com/buildx/working-with-buildx/) support
- Authenticated to GHCR:
  ```sh
  echo $GITHUB_TOKEN | docker login ghcr.io -u <username> --password-stdin
  ```

### Build

```sh
make build                  # build and push latest
make build VERSION=1.0.0    # tag a specific version
```

The `build` target automatically creates a `platypod-multiarch` buildx builder
(using the `docker-container` driver) on first run.

### Run locally

```sh
docker compose up
```

The app is available at [http://localhost:8080](http://localhost:8080).

<p align="right">(<a href="#readme-top">back to top</a>)</p>



<!-- USAGE -->
## Usage

| Variable | Description |
|----------|-------------|
| `YT_DLP_COOKIES_PATH` | Path to a Netscape-format cookies file for authenticated downloads |
| `YT_DLP_USERNAME` | Optional account username |
| `YT_DLP_PASSWORD` | Optional account password |

Downloaded media is written to `/app/downloads`.

<p align="right">(<a href="#readme-top">back to top</a>)</p>



<!-- CONTRIBUTING -->
## Contributing

Contributions are welcomed, either as issues tagged "enhancement" or pull requests. Ideally, please follow the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/#summary) standards.

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feat/<feature>`)
3. Commit your Changes (`git commit -m '<type>[optional scope]: <description>'`)
4. Push to the Branch (`git push origin feat/<feature>`)
5. Open a Pull Request

<p align="right">(<a href="#readme-top">back to top</a>)</p>



<!-- LICENSE -->
## License

Distributed under the MIT License. See `LICENSE.txt` for more information.

<p align="right">(<a href="#readme-top">back to top</a>)</p>



<!-- ACKNOWLEDGMENTS -->
## Acknowledgments

* [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) — the media downloader powering this project.

<p align="right">(<a href="#readme-top">back to top</a>)</p>



<!-- MARKDOWN LINKS & IMAGES -->
[contributors-shield]: https://img.shields.io/github/contributors/platypod/mediarvester.svg?style=for-the-badge
[contributors-url]: https://github.com/platypod/mediarvester/graphs/contributors
[forks-shield]: https://img.shields.io/github/forks/platypod/mediarvester.svg?style=for-the-badge
[forks-url]: https://github.com/platypod/mediarvester/network/members
[stars-shield]: https://img.shields.io/github/stars/platypod/mediarvester.svg?style=for-the-badge
[stars-url]: https://github.com/platypod/mediarvester/stargazers
[issues-shield]: https://img.shields.io/github/issues/platypod/mediarvester.svg?style=for-the-badge
[issues-url]: https://github.com/platypod/mediarvester/issues
[license-shield]: https://img.shields.io/github/license/platypod/mediarvester.svg?style=for-the-badge
[license-url]: https://github.com/platypod/mediarvester/blob/master/LICENSE.txt
