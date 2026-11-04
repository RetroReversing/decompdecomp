# decompdecomp public directory

A small Cloudflare Workers website that lists projects hosted by other people's apps.
It serves cached progress summaries and function maps; room traffic goes directly to the
owner's tunnel. No game files, source, assembly, agent transcripts, compilers or permutation
jobs are accepted or stored. The existing desktop app and judge remain independent.

## Storage and cost

- Workers Static Assets: the buildless website.
- Worker: publishing, directory search and a scheduled metadata fetcher.
- D1: project summaries, addresses, timestamps, publishing challenges and hashed removal keys.
- R2 Standard: the latest allowlisted function-map snapshot per project.

Start with Workers Free. R2 may require activating its subscription/payment method even
when usage remains inside its free allowance. Free quotas are shared with the account's
other applications; they are not a guarantee of free operation at arbitrary traffic.
The free Worker's CPU limit can also require upgrading for particularly large snapshots.
The project bounds snapshots at 4 MiB / 20,000 functions, responses at 50 projects per page,
and publishing requests at 30 per IP per day. Account-wide abuse controls may be needed
before opening submissions to a large audience.

Five hosts are refreshed every five minutes, oldest due first. Each successful project is
next due after 30 minutes; offline hosts back off to once per day. With more than roughly
30 continuously due projects, refreshes take longer. Increase the batch only after measuring
Worker CPU/subrequest limits and the desired spend. The UI always displays the saved time.
Source changes that do not change metadata do not rewrite the R2 snapshot.

## Run locally

Requires Node 24 and npm. From this directory:

```sh
npm ci
npx wrangler d1 migrations apply decompdecomp-directory --local
npm run dev
```

The placeholder database ID in `wrangler.jsonc` supports local development only. Local
state stays in ignored `.wrangler/`. The UI is available at the URL Wrangler prints.

```sh
npm test
npm run check
```

Tests exercise real SQLite tables and an in-memory R2 adapter. They cover proof of control,
private addresses/redirects, schema filtering, size limits, expiry, rate limits, removal,
offline retention and withdrawal. They never use game data or the running desktop app.

## Deploy to your Cloudflare account

1. Run `npx wrangler login` and authorize in your browser.
2. Run `npx wrangler d1 create decompdecomp-directory`.
3. Put the returned database ID into `wrangler.jsonc` (IDs are not credentials).
4. Run `npx wrangler r2 bucket create decompdecomp-directory-snapshots`.
5. Run `npx wrangler d1 migrations apply decompdecomp-directory --remote`.
6. Run `npm run deploy`. Wrangler prints the deployed `workers.dev` URL.

If you have several accounts, add the intended `account_id` to the config before creating
resources. No custom hostname is configured automatically. After verifying the deployed
site, attach `decompile.wtf` in Workers & Pages → decompdecomp-directory → Settings → Domains
& Routes → Add → Custom Domain. Keep `rooms.decompile.wtf` on its existing tunnel.
Do not change its DNS record to the directory Worker.

There is no separate always-on server, container, Postgres service or Git repository per
listed project. This version intentionally retains the latest snapshot, not project history.

## Publish a room

The host must use an app build containing `app/public_directory.py`.

1. Start sharing the project as **Public: anyone with the address**.
2. On the website, choose **List your project** and enter the public room link. A bare
   HTTPS origin works when exactly one room has opted into directory metadata.
3. Copy the generated code. In the app's **Decompile together → Public project directory**,
   enable **Allow public directory metadata**, paste the code and save.
4. Return to the website and choose **Verify and publish**.
5. Save the removal key. The browser also stores it locally for convenience.

Private invite links cannot be registered. Public room visibility by itself never opts a
project into the directory. Owners must save the newly generated 15-minute challenge in
the guarded desktop UI, proving control without giving the directory app/room credentials.
Each project needs its own code. A room's full link selects it when one host shares several.

To remove a listing immediately, use **Manage this listing** and its removal key. Disabling
metadata or making an open room private returns HTTP 410 and removes its listing on the
next refresh. If the host is unreachable or stops sharing, the last snapshot remains visible
as offline until removed. Browser/API caches may show removed data for up to 60 seconds.
Changing a quick tunnel address requires removing the old listing and publishing the new
one. Fixed tunnel hostnames avoid this inconvenience.

## Public contract

`GET https://HOST/directory/project?room=SLUG` returns JSON version 1:

- `room`, `title`, `platform`, `compiler`, `proof`;
- `summary`: function and matched counts, code and matched byte counts;
- `functions`: `[name, size, status, best_mismatch_score_or_null, instruction_count]` rows.

The Worker rebuilds this object from an allowlist before persisting it and strips `proof`.
Progress is self-reported by the host, not a new independent verification result. No directory
endpoint can join a room, claim functions, compile, change source or start agents. The public
website only links to `/join/open#room=…` on the original host.

Only public HTTPS hostnames on the default port are accepted. DNS addresses are checked on
every fetch, redirects are refused, request/response sizes and fetch timeouts are bounded,
and no cookies, authorization or invite secrets are forwarded. Requests always use the fixed
metadata path; arbitrary URL paths cannot be fetched. Hosts should expose this endpoint only
through their restricted room server, never their private app API.
