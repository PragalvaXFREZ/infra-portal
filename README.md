# infra-portal

Member profiles, writing and labs for the infra initiative, served at `infra.pragalva.me`.

Each member has a profile and can write posts. The app runs on
[devata](https://github.com/PragalvaXFREZ/lab), a Talos Kubernetes homelab, and it is built in stages so
that each stage can be read and rebuilt on its own.

## How this repository relates to devata

This repository builds the application and publishes one container image. It knows nothing about the
cluster that runs it.

| This repository | [`lab`](https://github.com/PragalvaXFREZ/lab) |
| --- | --- |
| Source, tests, `Dockerfile` | Which image digest runs on devata |
| Database schema and migrations | The Postgres instance and its storage |
| `compose.yaml` for local development | Secrets, routes, network policy |
| CI that publishes the image | Backups, alerts, dashboards |

The two sides meet at a set of environment variables (see [Configuration](#configuration)). The image is
the same everywhere. Only those values change. See [ADR 0001](docs/adr/0001-separate-app-repository.md).

## Run it locally

Requirements: Docker with Compose.

```bash
cp .env.example .env
docker compose up --build --wait
curl localhost:8000/livez
curl localhost:8000/readyz
```

Stop it with `docker compose down`. Add `--volumes` to delete the database as well.

## Continuous integration

`.github/workflows/ci.yaml` runs on every pull request and on every push to `main`:

1. **unit tests**: `uv run pytest`, against in-memory SQLite and a fake GitHub client.
2. **compose stack starts healthy**: starts `compose.yaml` with real Postgres, runs the migrations, calls
   both health endpoints, and checks that the models and migrations match.
3. **build image**: builds the image after both checks pass. On `main` it also pushes the image to
   `ghcr.io/pragalvaxfrez/infra-portal` with the tags `sha-<short commit>` and `main`, and writes the
   pinned reference (`tag@sha256:digest`) to the run summary.

Deployments pin the digest, not a tag. A tag can move. A digest always names the same image.

## Configuration

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DATABASE_URL` | yes | | `postgresql+psycopg://user:password@host:5432/db` |
| `SESSION_SECRET` | yes | | Signs the session cookie. At least 32 characters. `openssl rand -hex 32` |
| `BASE_URL` | no | `http://localhost:8000` | Public address. The OAuth callback is `BASE_URL/auth/github/callback`. |
| `GITHUB_CLIENT_ID` | no | empty | GitHub OAuth app. Empty disables sign-in. |
| `GITHUB_CLIENT_SECRET` | no | empty | GitHub OAuth app secret. |
| `COOKIE_SECURE` | no | `true` | Send the cookie over HTTPS only. Set `false` for `http://localhost`. |
| `ADMIN_GITHUB_LOGINS` | no | empty | Comma-separated GitHub logins that sign in as admins without an invite. |
| `CONTACT_EMAIL` | no | `pragalva.sapkota@deerwalk.edu.np` | Shown to people who sign in without an invite. |
| `RESEND_API_KEY` | no | empty | [Resend](https://resend.com) API key for follower and subscriber email. Empty hides the Follow and Subscribe forms. |
| `MAIL_FROM` | no | empty | Sender, such as `infra <posts@pragalva.me>`, on a domain verified in Resend. |

`compose.yaml` builds `DATABASE_URL` from the `POSTGRES_*` values in `.env`. The migration step receives only
`DATABASE_URL`.

## Sign-in

Sign-in uses GitHub OAuth and is invite-only. See [ADR 0003](docs/adr/0003-github-sign-in-invite-only.md).

1. Create a GitHub OAuth app (GitHub Settings, Developer settings, OAuth Apps). Set the callback URL to
   `http://localhost:8000/auth/github/callback` for local use.
2. Put its client ID and a new client secret in `.env`, and your GitHub login in `ADMIN_GITHUB_LOGINS`.
3. Run `docker compose up --build --wait`, open <http://localhost:8000>, and sign in. The link is
   "Member sign in" at the foot of the page, or in the menu on a phone.
4. Invite others at `/admin/members` by GitHub login. Their first sign-in claims the invite.

A GitHub OAuth app has one callback URL, so production uses a second app with
`https://infra.pragalva.me/auth/github/callback`.

## Profiles and posts

| Page | Who | What |
| --- | --- | --- |
| `/` | everyone | What the initiative is, recent labs, recent posts, members |
| `/labs`, `/labs/<slug>` | everyone; hidden pages only for members or admins | Labs, experiments and learning sessions |
| `/learn`, `/learn/<slug>` | members; drafts only for admins | The learning path: stages, their chunks and builds, and the member's own ticks |
| `/writing` | everyone | All published posts |
| `/subscribe` | everyone | The form to get an email when anyone posts. Every Subscribe button leads here |
| `/members` | everyone | Members who have signed in |
| `/u/<login>` | everyone | Profile and published posts |
| `/u/<login>/<slug>` | everyone, or only the author for a draft | One post |
| `/write`, `/posts/<id>/edit` | the author | Write, preview, save, publish, unpublish, delete |
| `/me/posts` | the author | All own posts, drafts included |
| `/settings/profile` | the member | Name, website, Markdown bio |
| `/subscription/<token>` | the subscriber, from a link in an email | Stop one member's posts, start them again, or stop all emails |
| `/admin` | admins | Overview: people, steps still hidden, and where each member is on the path |
| `/admin/course` | admins | The stages and their steps; show them to members or hide them; import the course notes |
| `/admin/labs` | admins | Write, preview, show or hide, and delete database pages, steps of the path included |
| `/admin/members` | admins | Invite and remove members |
| `/admin/mail` | admins | Preview every email the site sends, and send the samples to one address |

Every admin page has the same row of tabs, and the account menu has one link to them, Admin.

Posts and bios are Markdown. Raw HTML in them is escaped, and `javascript:` links are not rendered. A post's
link is set when the post is created and does not change when the title changes. Admins can delete any
post.

The editor has Write and Preview tabs. Preview renders the post with the same Markdown renderer as the
published page and saves nothing.

## Following by email

Anyone can ask for an email about new posts in two ways:

- **Follow one member**, from the form on that member's page and under each of their posts.
- **Subscribe to everyone**, from a Subscribe button: at the right end of the bar at the top of every
  page, on the card in the home page's opening section, beside the heading on `/writing`, under the follow
  form and at the foot of the page. One sign-up covers every member, including members who join later. A
  subscriber can stop emails about one member and keep the rest.

Each Subscribe button is a link to `/subscribe`, a page with the form. With JavaScript
(`static/subscribe.js`) the button opens the same form in a pop-up instead and sends it without leaving the
page. Beside the form the pop-up shows its own painting, a reader with a book (Corot, *A Woman Reading*), not
the painting of the page behind it. `/any-page#subscribe` opens the
pop-up directly, so that address can be shared. The pop-up is left out of the pages that manage a
subscription.

Both work only when `RESEND_API_KEY` and `MAIL_FROM` are set; without them the forms are hidden.

1. The address gets a confirmation link first. Nothing else is sent until it is opened. Asking again within a
   day sends no second link, and the page reads the same whether or not the address already follows or
   subscribes, so the forms do not reveal who signed up. A hidden field catches form-filling bots.
2. Confirmation emails to new addresses are capped at 20 an hour: per member for the follow form, and for
   the whole site for the subscribe form. Requests over the cap get the same page and no email.
3. The first time a post is published, each address gets one email with the title and link. The readers are
   the author's confirmed followers and the confirmed subscribers who have not stopped that author. An
   address that is both gets one email, the subscriber one. Unpublishing and publishing again sends nothing.
   Posts published before this feature count as already announced. Only posts are emailed.
4. Every post email has an unsubscribe link and a `List-Unsubscribe` header. The links open a page with
   buttons, because mail scanners open links.
   - A follower's link (`/unsubscribe/<token>`) offers one button that stops that member's posts.
   - A subscriber's link (`/subscription/<token>?author=<member id>`) offers "Stop emails about this member"
     and "Stop all emails". The page also lists every member, each with a button to stop their emails or to
     start them again, so a reader can drop one member without waiting for an email about them.
     Stopping one member also removes a follow of that member by the same address.
   - Mail apps' one-click unsubscribe (RFC 8058) posts to the same address as the link. For a follower it
     stops that member's posts. For a subscriber it ends the subscription and every follow of the same
     address, because a mail app's button promises no more mail from the sender.
5. Following a member that the same address stopped on its subscription starts that member's posts again.

The emails go out after the response, through Resend's batch API. A failure is logged and the post stays
published. Authors see on `/me/posts` how many addresses get an email when they publish.

Admins can check the emails at `/admin/mail`. It shows each of the four messages (confirm a follow, confirm a
subscription, new post to a follower, new post to a subscriber) with made-up data: the subject, the headers,
the HTML part and the text part. A form sends all four to one address through the real mailer. The samples
are built by `mail.sample_emails`, the same code as the real messages, and the tests check them.

## Labs and experiments

A lab page has a kind: `lab` (a bigger build), `experiment` (something we tried and wrote up) or `session` (a
learning session: videos, questions and a short warm-up). Pages come from two places, and `/labs` and the
home page show them as one list, newest first.

### File labs: always public

Files in `src/infra_portal/content/labs/`. Add one with a pull request that adds a file. The file name is the
address: `ci.md` is served at `/labs/ci`.

```markdown
---
title: CI, one step at a time
kind: lab            # lab, experiment or session
date: 2026-10-09
link: https://github.com/...   # optional
summary: One sentence for the list.
---
Body in Markdown.
```

A file with a bad header fails the tests, so a broken lab never reaches the image. The files are read once at
start-up.

### Database labs: hidden until they are ready

This repository and its container image are public. Anything in a file here can be read by anyone, so a
page that must stay hidden cannot be a file. Those pages are rows in the `lab_pages` table instead, written by
an admin at `/admin/labs`. Each row has a visibility:

| Visibility | Shown in the form as | Who sees it |
| --- | --- | --- |
| `draft` | Draft | Admins only. Every new page starts here. |
| `members` | Members only (approved) | Any signed-in member |
| `public` | Public (finished) | Everyone |

- One function, `labs.can_see`, decides what a viewer may see, and `/labs`, `/labs/<slug>` and the home page
  all list through it. Database rows are read on every request, so a change shows at once.
- A page you may not see answers `404`, the same answer as an address that does not exist, so an address
  does not reveal that a hidden page is there.
- Admins see every page. Pages that are not public carry a small "Draft" or "Members only" label; members see
  "Members only" on the pages they can see. Signed-out visitors get one line on `/labs` saying that members
  see more after signing in. It is the same line whether or not anything is hidden.
- The address is set once, when the page is created, like a post's. It may not be the address of a file
  lab. If a file lab later takes the address of a database page, the file wins.
- The editor has the same Write and Preview tabs as the post editor. Removing a member keeps the pages
  they edited.

## The learning path

The path is the course for members: numbered stages, and in each stage its steps in order. A step is a
**chunk** (a few videos, questions and a warm-up at the keyboard) or a **build** (the hands-on work that uses
the chunks before it). It is at `/learn` and only signed-in members see it.

- A stage is a row in `course_stages`. A step is a `lab_pages` row with a stage and a place in it, so steps
  are written in the same editor as database labs and follow the same visibility rule.
- A member sees a step when they may see both the step and its stage. A draft stays with the admins.
  Hidden, missing and signed out all get the same 404.
- A step with no text yet is listed as "Not written yet" and cannot be opened. A stage with nothing written is
  shown as coming later.
- Steps are left out of `/labs` and the home page. Written builds get their own part of `/labs` for those
  who may see them.
- A member can tick a step as done and take the tick back (`page_progress`). A tick is the member's own
  word: it unlocks nothing. The path shows the next step that is not ticked, and admins see where each member
  is on `/admin`.

### Getting the course onto the site

The course is written as Markdown notes in a private notes repository. This repository and its image are
public, so the notes are **not** files here. An admin uploads them at `/admin/course/import`:

1. Choose the note files and press Import. Everything new is stored as a draft.
2. Read the drafts as an admin on `/learn`.
3. On `/admin/course`, press "Show the stage and its written steps to members". Single steps can be shown or
   hidden too.

What the import reads, and nothing else:

| Note | Recognised by its title | What is stored |
| --- | --- | --- |
| Stage note | `..., stage 2: Bread` | Each `## Chunk N. Title` section as a chunk; each `### Build N. Title` heading as the name of a build |
| Build note | `Stage 2, build 1: A first loaf` | The whole note, as that build |
| Warm-up note | `Stage 2, chunk 3: warm-up` | Added to the end of chunk 3 |
| Any note | A table starting with the columns `Stage` and `Topic` | The names of the stages |

A file whose name ends in `-mentor.md`, or whose header has `draft: true`, is an answer key and is refused.
Comments that the notes app hides (`%% ... %%` and `<!-- ... -->`) are dropped. A chunk ends at the next
first or second level heading.

Uploading again replaces a step's text and title; who sees it and the members' ticks stay. The order of a
stage changes only when its stage note is in the upload, and a chunk keeps its warm-up when the stage note
comes without the warm-up note. A stage that already exists is left as it is. Links between notes (`[[folder/stage-2-build-1|the build]]`) become
links between pages when the reader may see both.

### Notes syntax in lab bodies

Lab guides are first written in Obsidian, so lab bodies (files and database pages, not posts or bios) also
understand two pieces of its syntax:

- Wikilinks, `[[some/path|Label]]` or `[[some/path]]`. Their targets are notes elsewhere, so only the label,
  or the last part of the path, is shown, as plain text. In a table, write the separator as `\|`.
- Callouts, a quote whose first line is `> [!warning] Optional title`. The types `note`, `info`, `tip`,
  `important`, `warning` and `caution` each have a look; any other type looks like `note`. Without a title,
  the type is the title.

Raw HTML stays escaped here too, in the body, the callout title and the wikilink label.

## Database migrations

Alembic manages the schema. The image runs migrations as a separate one-shot step, before the app starts:

```bash
docker compose run --rm migrate                                          # apply
docker compose run --rm migrate python -m infra_portal.migrate check     # models match migrations?
```

After a model change, generate a migration against a running database and review it before committing:

```bash
uv run alembic revision --autogenerate -m "describe the change"
```

Keep migrations additive. During a rollout the old and the new version run at the same time, so a column
must stop being used in one release before a later release drops it.

## Health endpoints

| Endpoint | Meaning | Touches the database |
| --- | --- | --- |
| `GET /livez` | The process is up and answers HTTP. | No |
| `GET /readyz` | The process can do useful work. Returns `503` when Postgres is unreachable. | Yes |

They are separate on purpose. If the liveness check depended on the database, a database outage would
make the orchestrator restart every API container, and the restarts would fix nothing. A failing
readiness check only removes the container from traffic until the database returns.

## Development without Docker

```bash
uv sync
uv run pytest
```

## Stages

Each stage ends with a git tag. Check out a tag to see the project at that point.

| Tag | Adds | Status |
| --- | --- | --- |
| `v0-compose` | API and Postgres in Compose, health endpoints, non-root multi-stage image | Done |
| `v1-ci` | CI runs tests, builds the image, and publishes it to GHCR | Done |
| `v2-login` | GitHub sign-in, invite-only members, sessions, Alembic migrations | Done |
| `v3-profiles-posts` | Profiles, member directory, Markdown posts with drafts, admin moderation | Done |
| `v4-on-devata` | Runs on devata through Argo CD at `infra.pragalva.me` | Planned |

## Layout

```
src/infra_portal/   application package
  migrations/       Alembic migrations
  templates/        Jinja2 pages
  content/labs/     public labs, experiments and sessions, one Markdown file each
  static/           CSS, the background painting (art/), self-hosted fonts (fonts/)
tests/              pytest suite
docs/adr/           architecture decision records
Dockerfile          two-stage image build
compose.yaml        local stack: db, migrate, api
.github/workflows/  CI
```
