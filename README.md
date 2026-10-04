# Agent Runtime

A Docker-based development runtime for Codex and Multica, with a persistent private HOME for each instance. Multiple agents belonging to the same trusted user can work in one container.

- **Ready-to-use tools:** Codex, Multica, Lark CLI, miniprogram-ci, Playwright CLI and Chromium, plus official Playwright and Lark skills.
- **Development toolchain:** Go, Node.js/npm/pnpm, Python/uv, C/C++, Git, gh, SSH and rsync.
- **Persistent user state:** projects, credentials, sessions, user-installed tools and custom skills stay in HOME across container recreation.
- **Optional sharing:** download caches and read-only Codex API-key configuration, without sharing HOME.

Image: `ghcr.io/creekxi2026/agent-runtime:latest` for `linux/amd64` and `linux/arm64`. Install project dependencies separately from their lockfiles. The image does not include application code, Docker/Podman or database servers.

[Quick Start](#quick-start) · [Storage](#storage) · [Configuration](#configuration) · [Usage](#usage) · [Updates](#updates) · [Development](#development)

## Quick Start

You need Docker Engine, Docker Compose v2, and access to Multica and a model service.

### 1. Prepare an instance

A new instance needs only the Compose file and environment file:

```bash
mkdir -p agent-runtime/user01
cd agent-runtime/user01
SOURCE=https://raw.githubusercontent.com/creekxi2026/agent-runtime/main
curl -fL "$SOURCE/compose.yaml" -o compose.yaml
curl -fL "$SOURCE/runtime.env.example" -o .env
chmod 600 .env
```

Edit `.env` and give `COMPOSE_PROJECT_NAME` a **unique instance name**. Keep credentials out of Git and chat; quote real secret values with single quotes in `.env`. Do not overwrite an existing credential-bearing environment file.

### 2. Choose storage before logging in

The default is a private Docker-managed HOME volume, with no shared mounts. If that suits your instance, continue below. For a host-directory HOME, shared caches or shared Codex credentials, **complete [Storage](#storage) first**, including the override downloads and `COMPOSE_FILE` setting. Login commands initialize the selected HOME; changing storage later does not migrate those files.

### 3. Authenticate

**Multica:** the default connects to Multica Cloud. If `MULTICA_BOOTSTRAP_TOKEN` is set, skip interactive Multica login; daemon startup applies the token and configured URLs.

Otherwise, for a self-hosted instance, set the endpoints **before the first interactive login**. Replace these example URLs with the corresponding `.env` values; omit `app_url` if no application URL is configured. The `.env` URLs are applied automatically only when starting the daemon, not by the interactive login command.

```bash
docker compose run --rm runtime multica config set server_url 'https://multica-api.example.com'
docker compose run --rm runtime multica config set app_url 'https://multica.example.com'
```

Without a bootstrap token, log in to Multica:

```bash
docker compose run --rm runtime multica login
```

**Codex:** log in only when using private Codex credentials. **Skip this command in shared Codex mode**, and do not run `codex login` or `codex logout` against shared credentials. For private API-key authentication, `codex login --with-api-key` reads the key from standard input.

```bash
docker compose run --rm runtime codex login
```

### 4. Start the runtime

```bash
docker compose up -d
docker compose logs --tail=100 runtime
```

The daemon requires Multica authentication and refuses to start without it. Model access must also be configured for the tasks you intend to run; starting the container alone does not verify model authentication.

## Storage

Relative bind paths are resolved from the directory containing the first Compose file. Use named volumes by default; choose a bind mount when you need direct access to host files.

| Data | Source | Container path | Access |
| --- | --- | --- | --- |
| Private HOME | Named volume `<COMPOSE_PROJECT_NAME>-home`, or `HOME_VOLUME`; optional bind via `HOME_DIR` | `/home/agent` | Read/write |
| Download caches | Private HOME by default; optional volume `agent-runtime-download-cache` or bind via `SHARED_CACHE_DIR` | `/shared/caches` when enabled | Read/write |
| Shared Codex configuration | Optional dedicated directory via `SHARED_CODEX_DIR` | `/shared/codex` | Read-only |

HOME holds configuration, credentials, sessions, `workspace`, user-installed tools and custom skills. **Never reuse HOME across independent instances**, including when setting `HOME_VOLUME`. Image-managed tools live under `/opt`; the HOME volume uses `nocopy`, so it does not copy image HOME contents into persistent storage.

### Select overrides

Download each required override from the same `SOURCE` URL used in Quick Start and place it beside `compose.yaml`. For example:

```bash
curl -fL "$SOURCE/compose.bind-home.yaml" -o compose.bind-home.yaml
```

Then set the following values in `.env`:

| Storage choice | Required override files | Environment settings |
| --- | --- | --- |
| Default private HOME volume | None | Leave `COMPOSE_FILE` unset |
| Private HOME bind | [compose.bind-home.yaml](compose.bind-home.yaml) | `HOME_DIR=./home`; `COMPOSE_FILE=compose.yaml:compose.bind-home.yaml` |
| Shared cache volume | [compose.shared-cache.yaml](compose.shared-cache.yaml) | `COMPOSE_FILE=compose.yaml:compose.shared-cache.yaml`; optionally set `SHARED_CACHE_VOLUME` |
| Shared cache bind | [compose.shared-cache.yaml](compose.shared-cache.yaml), then [compose.shared-cache.bind.yaml](compose.shared-cache.bind.yaml) | `SHARED_CACHE_DIR=../shared/caches`; `COMPOSE_FILE=compose.yaml:compose.shared-cache.yaml:compose.shared-cache.bind.yaml` |
| Read-only shared Codex | [compose.shared-codex.yaml](compose.shared-codex.yaml) | `SHARED_CODEX_DIR=../shared-codex`; `COMPOSE_FILE=compose.yaml:compose.shared-codex.yaml` |

**Ordering matters:** put `compose.yaml` first and each bind override after the corresponding named-volume configuration. On Windows, use `;` instead of `:` as the `COMPOSE_FILE` separator. Once set, use ordinary `docker compose` commands for login, startup, shells and updates; do not supply a separate `-f` list that omits your overrides.

Choices can be combined. For a HOME bind, cache bind and shared Codex directory, download all four overrides and use:

```dotenv
COMPOSE_FILE=compose.yaml:compose.shared-cache.yaml:compose.shared-codex.yaml:compose.bind-home.yaml:compose.shared-cache.bind.yaml
HOME_DIR=./home
SHARED_CACHE_DIR=../shared/caches
SHARED_CODEX_DIR=../shared-codex
```

Setting `HOME_DIR` alone does not switch storage: `compose.bind-home.yaml` must be enabled. Neither changing a variable nor adding an override migrates existing data.

### Shared download caches

Only npm, Go module and uv download caches are shared. Global tools, `node_modules`, virtual environments, Go build caches and credentials remain private. Startup initializes only the `npm`, `go-mod` and `uv` directories to UID/GID `1000:1000` with mode `0750`; it does not recursively change their contents and rejects symlinks or non-directory entries. uv uses `copy` to avoid cross-filesystem hard links. Do not clean the uv cache while installing dependencies.

Share only among mutually trusted Linux containers using the same UID. Containers using the same cache volume name or bind source can read and modify each other's caches, which may contain private source code. Do not mount or copy macOS caches, or store credentials in the cache root.

### Shared Codex credentials

Before any login or startup command, create a **dedicated directory** containing `config.toml` and `auth.json`, readable by UID/GID `1000:1000`. Use the [configuration template](shared-codex/config.toml.example) and [authentication template](shared-codex/auth.json.example) as a starting point. A missing source directory is not created automatically.

Only these two files are linked into private Codex state. **Do not share the entire `.codex` directory**; sessions and caches stay in private HOME. Existing private files are backed up with the `.before-shared` suffix; startup refuses to overwrite conflicting backups.

This mode supports **API keys only**, not ChatGPT OAuth credentials that require refresh writes. All participating instances can read the shared key; read-only access is not confidentiality. Skip `codex login/logout`. Atomic replacement of the shared files makes them available to subsequent reads, but does not guarantee live updates for in-flight tasks. Shared defaults are not an enforced policy.

## Configuration

See [runtime.env.example](runtime.env.example) for available settings and [compose.yaml](compose.yaml) for the base deployment contract.

| Setting | Purpose / default |
| --- | --- |
| `COMPOSE_PROJECT_NAME` | Required unique instance identity |
| `RUNTIME_IMAGE` | Image selection; defaults to `ghcr.io/creekxi2026/agent-runtime:latest` |
| `HOME_VOLUME` | Private volume name; defaults to `<COMPOSE_PROJECT_NAME>-home` |
| `CPU_LIMIT`, `MEMORY_LIMIT` | Defaults: `2` CPUs and `4g` memory |
| `MULTICA_SERVER_URL`, `MULTICA_APP_URL` | Leave blank for Multica Cloud; set for self-hosting |
| `MULTICA_BOOTSTRAP_TOKEN` | Optional alternative to interactive Multica login |
| `MULTICA_DAEMON_MAX_CONCURRENT_TASKS` | Optional daemon-wide limit; unset uses Multica's native default, independently of the UI's per-Agent concurrency limit |
| `LARK_APP_ID`, `LARK_APP_SECRET`, `LARK_BRAND` | Optional Lark application initialization; brand defaults to `feishu` |

The startup entrypoint briefly uses root to initialize private directories, then runs the application as UID/GID `1000:1000` with capabilities cleared. Compose relaxes seccomp/AppArmor for Codex's inner sandbox, but does not enable privileged mode or mount the Docker socket. This is **not a strong isolation boundary for untrusted tenants**.

## Usage

Open a shell as the application user:

```bash
docker compose exec --user 1000:1000 runtime bash
```

Container consoles and `docker compose exec` may otherwise default to root. Use the explicit UID for diagnostics and user-tool installation to avoid root-owned files in HOME.

- **User tools:** install with `npm install -g package-name` or `uv tool install package-name`; these installations persist in HOME. User command paths include `~/.local/bin`. `MULTICA_CODEX_PATH` selects the Codex executable used by Multica.
- **Skills:** official skills are linked from `/opt/agent-skills` into `~/.agents/skills`. Put custom skills in `~/.agents/skills/skill-name/SKILL.md`; updates preserve custom content. Multica's `Copy from a runtime` creates a snapshot, not a live link.
- **Browser sessions:** use `playwright-cli -s=task-name open about:blank`. Give concurrent tasks separate sessions and close only your own session, not `close-all` or `kill-all`. The default idle timeout is one hour. Files from `state-save` / `state-load` contain sensitive session data and belong in private HOME.
- **Lark user resources:** `.env` can initialize application credentials, but user authorization additionally requires `docker compose run --rm runtime lark-cli auth login --domain docs --domain drive`.
- **WeChat Mini Programs:** preview/upload requires a project AppID, an upload private key and the corresponding WeChat-side configuration. A read-only root filesystem is not supported.

## Updates

Wait for running tasks to finish and back up HOME and `.env`. Keep the **same instance name, HOME backing store and `COMPOSE_FILE`** when updating:

```bash
docker compose pull
docker compose up -d
```

Recreation reuses HOME; other writable container-layer files do not persist. Ordinary `docker compose down` keeps volumes. **`docker compose down -v` or deleting volumes destroys data**, and removing a shared cache volume can also affect other instances. HOME backups contain credentials and must be protected as sensitive data.

The [publish workflow](.github/workflows/publish.yml) checks the base image, tools, official skills and Chromium/zipalign dependencies daily. Changes are published as `latest` after verification on both architectures. It **does not update running containers automatically**. Only `latest` is retained; there are no historical tags or automatic rollback. The image's dependency manifest is at `/usr/share/agent-runtime/dependencies.json`.

## Development

Run the unit tests and build from the checked-in dependency manifest:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/split_dependencies.py --manifest runtime-deps.json --output .build-inputs
BASE_IMAGE=$(python3 -c 'import json; print(json.load(open("runtime-deps.json"))["base_image"])')
docker build --build-arg "BASE_IMAGE=$BASE_IMAGE" -t agent-runtime:test .
```

Container tests need Docker and disposable test resources. Run them on an isolated test host: the smoke scripts use fixed resource names and cleanup commands.

```bash
IMAGE=agent-runtime:test sh tests/smoke.sh
SHARED_CACHE_DOCKER_TEST=1 python3 -m unittest discover -s tests -p test_shared_cache.py -v
python3 tests/shared_cache_smoke.py
```

The npm/Go/uv fixture test requires the current image and `host.docker.internal`. The [verify workflow](.github/workflows/verify.yml) covers tools, mounts, private state, shared configuration, skills and browsers. Offline fixtures use no real credentials and **do not prove** model authentication, Multica task dispatch, Lark authorization or Mini Program upload success.
