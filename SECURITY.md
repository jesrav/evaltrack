# Security Policy

## Reporting a vulnerability

Report vulnerabilities privately, not as a public issue:

- Preferred: open a [GitHub Security Advisory].
- Or email **mail@jesravnbol.dk**.

I will acknowledge within a few days and keep you informed about a fix.

evaltrack is pre-1.0. Security fixes go to the latest 0.x minor.

## What a run holds

Recorded runs contain whatever your eval tasks produce, including data you never meant to persist.
Treat a run repository with the same sensitivity as the data your evals touch.

Prefer plain JSON-serializable values for task outputs and case metadata. Any other value is still
stored at save time. A pydantic model or dataclass is stored as a dict of its fields, binary bytes
as a fingerprint, a reference cycle as a marker, and any other object as its `repr()`. Only the
model holding such a value is flattened, to its raw field values, so computed fields and custom
field serializers do not apply. A clean model, even one nested inside it, is stored through
pydantic's own serializer. A model field marked `exclude=True` is left out.

The remaining fields and the `repr()` can hold data you never meant to persist. A model that holds
an API client, for example, can carry credentials into the stored run through its fields, and they
land in the run repository like any other recorded value.

## Dashboard trust model

`evaltrack ui` is an unauthenticated server on `127.0.0.1`. Anything that runs on your machine can
read and delete through it. That reaches every repository the dashboard has mounted, including the
shared `remote` your CI pushes to.

These protections always apply:

- **It listens on your own machine only, and no flag changes that.** Nothing on your network can
  connect to it. There is no option to open it up, because a dashboard with no login has no safe way
  to offer one.
- **A delete has to come from the dashboard's own page.** The server reads the `Origin` header to
  check, so a site open in another tab cannot delete through your browser. No page can load the
  dashboard inside a frame either.
- **`baseline` cannot be deleted through the API.** Neither can a run that the history of any ref
  still reaches, so a promoted run always stays out of reach.
- **The confirm dialog names the repository and its URL.** It is there for misclicks, not attacks.

Not covered:

- Any process or user on the same host. Nothing authenticates.
- A local server you do not trust, running beside the dashboard.
- The app served some other way. Whoever serves it owns the bind.

---

**Related:** [CLI › evaltrack ui] · [Repositories › Cleaning up runs]

[github security advisory]: https://github.com/jesrav/evaltrack/security/advisories/new
[cli › evaltrack ui]: docs/cli.md#evaltrack-ui
[repositories › cleaning up runs]: docs/repositories.md#cleaning-up-runs
