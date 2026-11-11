<!-- managed by nexera-guardrails · guardrails-version: 0.3.15 · rewritten by sync-rules; put your own rules in claude-security-guidance.local.md -->
# Review method: find the trust boundaries first

Before looking for violations, establish where the lines are. Every check below is about something
crossing one, and a crossing cannot be spotted until the line is known. Ask, in this order:

1. **Where does untrusted input enter?** A request, a queue, a file drop, `argv`, an environment
   variable, a database another system writes, a webhook, a scanned document.
2. **Whose data is it?** Is there more than one tenant, user or account whose records must not
   reach each other?
3. **What leaves, and to whom?** A response, a log sink, a published artifact, a report, a file, a
   third-party call.
4. **What is privileged?** Credentials, money, one-time resources, anything that cannot be undone.

The vocabulary is from request/response services; a pipeline, CLI, library, daemon or batch job has
the same boundaries spelled differently. Translate before looking.

## Authorization and data isolation — the highest-value class, and the most often missed

- For every path that accepts a caller-chosen identifier, confirm ownership is enforced **where the
  record is fetched**, not only at an outer layer that another entry point can reach around.
- A read, update or delete keyed only on a record ID with no owner predicate is a finding.
- An identity taken from caller input and trusted without checking it against the authenticated
  actor is a finding.
- Check bulk paths specifically: listing, search, export, report, backup, bulk download. They leak
  across owners far more often than single-record paths, and are reviewed less.

## Business logic

- State transitions that can be skipped; operations that are not idempotent where a retry is
  possible; races on anything that moves money or consumes a one-time resource.
- Missing re-authentication before a change that would let an attacker keep access: the address a
  credential reset goes to, the destination money is paid to, the keys themselves.

## Configuration

- Defaults that are permissive when unset: authentication optional, verification disabled, debug on.
- Cross-origin policy widened to a wildcard, or reflecting whatever origin asked.
- Secrets or hostnames for one environment committed into the configuration of another.

## Evidence

- Cite the file and line. If exploitability cannot be confirmed, say that rather than asserting it —
  a fabricated vulnerability costs a real investigation.
- One root cause can list many affected call sites; report it once.

## Do not report

Denial of service or resource exhaustion; rate limiting unless the change removes a limit that was
there; input validation with no demonstrated path to impact; open redirect. This review cannot
establish these from a diff, and an unsubstantiated finding costs more than a missed one.
