# 0027 — Public domain, vendor branding, and AWS-native edge

- **Status:** accepted
- **Date:** 2026-05-08
- **Decision-makers:** the maintainer
- **Tags:** #marketplace #branding #infra #aws

## Context and problem statement

The Marketplace listing, security review, and DPD/DUO questionnaire all require publicly accessible URLs for privacy policy, terms of service, security disclosure, and a support email at your vendor domain. Placeholders like `https://example.com/...` and `support@example.com` stand in for that domain until you register your own.

Two things changed:

1. **`example.com` was registered** via Route53 on 2026-05-08 specifically to host product-facing materials. The intent (per the conversation that led to this ADR) is to separate product brand from the personal domain so the Marketplace listing reads as a real business, not a developer's portfolio site.
2. **AWS-first stance was reaffirmed.** Edge services (static hosting, email, TLS) should default to AWS unless there is a clear reason not to.

This ADR locks in the vendor/product branding split, the URL layout, and the AWS services backing them, so the find-and-replace and infra work can proceed against a single approved decision instead of being re-litigated mid-PR.

## Considered options

This ADR groups five linked decisions; each is documented individually so future revisits can update one without unwinding the rest.

### 1. Vendor entity vs product brand

- **A. Single brand:** vendor and product both named "Jira Flow Intelligence".
- **B. Vendor + product split:** vendor "Example", product "Jira Flow Intelligence". Mirrors Atlassian/Jira, Linear/Insights, Salesforce/Slack.
- **C. Rename vendor:** legally rebrand the vendor entity to Jira Flow Intelligence.

### 2. API URL

- **A. Keep App Runner URL** (`uefwzjtixk.us-east-1.awsapprunner.com`) as-is.
- **B. Custom domain** at `api.example.com` via App Runner's native custom domain feature + ACM cert.

### 3. Subdomain layout

- **A. Apex + paths:** `example.com/docs`, `/privacy`, `/terms`, `/security`. One zone, one cert, one analytics property.
- **B. Split subdomains:** `docs.example.com`, `privacy.example.com`, etc. Multiple certs and CDN distributions.

### 4. Docs-site hosting

- **A. AWS Amplify Hosting.** Git-connected, auto TLS via ACM, branch previews, native Next.js (incl. SSR if ever needed). ~$1–5/mo at docs-site scale after the 12-month free tier.
- **B. S3 + CloudFront.** Cheapest at runtime if the site is `next export`-static. Build pipeline wired manually via CodeBuild or GitHub Actions OIDC → S3.
- **C. App Runner.** Possible but oversized for a static landing site.
- **D. Non-AWS (Vercel / Cloudflare Pages).** Excluded by AWS-first stance.

### 5. Email for `support@`, `security@`, `legal@`

The AWS-first stance applies to infrastructure (compute, data, edge, observability), not to collaboration tooling where managed SaaS is the obvious correct call. Email client + mailbox management falls in the second category.

- **A. SES inbound + Lambda forwarder.** AWS-native. Free tier covers 1000 inbound emails/month. Forwards to a personal inbox; no real mailboxes, no Gmail/Calendar/Drive integration, custom-from-header trickery to make replies appear branded.
- **B. AWS WorkMail.** Paid per user. Real mailboxes but the client is dated and the productivity-suite integrations don't exist.
- **C. Google Workspace Business Starter.** Paid per user. Real Gmail mailboxes, Calendar, Drive. Aliases let one paid user host `support@`, `security@`, `legal@`. Best-in-class client and admin console.
- **D. Non-AWS, non-Workspace (Cloudflare Email Routing / ImprovMX).** Excluded — Workspace is the productivity-suite choice; mixing in a third-party forwarder adds surface area for no gain.

## Decision

**1. Vendor + product split (Option B).** Vendor stays "Example"; product is "Jira Flow Intelligence". `app_vendor_name` in `backend/app/core/config.py` stays `"Example"`. `app_vendor_url` updates to `https://example.com`.

The Atlassian-side identity is the **Forge ARI** in `forge-prod/manifest.yml` (`app.id: ari:cloud:ecosystem::app/00000000-...`), assigned by Atlassian at `forge register` time. The ARI is immutable for the life of the app; installations (including a pilot install) bind to it, not to any string we control. It already contains no "example" reference, so no action is needed there.

The backend's `app_key` field (`com.example.flow-intelligence` in `config.py`) is declared once and **referenced nowhere else** — it is dead metadata. It will be deleted as part of the find-and-replace PR rather than renamed; if a future need for an internal reverse-DNS string emerges, we'll reintroduce it as `com.flow-intelligence.app`. Either way, this is invisible to existing Forge installs.

**2. Custom API domain (Option B).** `api.example.com` becomes the canonical backend URL. App Runner custom domain feature + ACM cert. Forge `forge-prod/manifest.yml` `remotes:` baseUrl updates accordingly. The original App Runner URL keeps working as a fallback.

**3. Apex + paths (Option A).** All public materials live under `example.com/...`:

```
https://example.com/             — landing page
https://example.com/docs         — user manual
https://example.com/privacy      — privacy policy
https://example.com/terms        — terms of service
https://example.com/security     — security disclosure
```

**4. AWS Amplify Hosting (Option A).** The docs site is a Next.js project (Cruip Open PRO + Docs templates), deployed via Amplify connected to a dedicated static-site folder (or a sibling repo if it grows independent). Amplify is defined in CDK as a new stack `a dedicated static-site CDK stack`. If runtime cost ever materializes as a concern, downgrading to S3 + CloudFront via `next export` is straightforward — both options work for a fully-static site. We start on Amplify for ergonomics.

**5. Google Workspace Business Starter (Option C).** Rationale above: collaboration tooling is the right place to use managed SaaS even under AWS-first.

Setup:
- One paid user account (`maintainer@example.com` or similar — owner picks the primary identity).
- `support@`, `security@`, `legal@` configured as **email aliases** on that user. All three deliver to the same inbox; send-as is configurable per alias from the Gmail UI. ~$84/year.
- DNS records on the existing Route53 hosted zone for `example.com`:
  - MX records → Google's mail servers (`smtp.google.com` MX targets, exact records provided by Google during domain verification).
  - SPF: `v=spf1 include:_spf.google.com ~all`.
  - DKIM: keys generated in Google Admin → published as TXT in Route53.
  - DMARC: `_dmarc` TXT record with a policy of `p=quarantine; rua=mailto:postmaster@example.com` to start.
  - Domain verification: Google-provided TXT record at the apex.
- DNS lives in CDK as part of `a dedicated static-site CDK stack` (the same stack that owns the `example.com` hosted zone, Amplify domain, and ACM cert), or split into a small `dns_stack.py` if it grows. No `email_stack.py` is needed — there is no AWS-side email infrastructure.

## Consequences

### Positive

- One AWS bill for hosting, email, DNS, compute, data, observability — consistent with the Phase 1 infra story.
- TLS via ACM everywhere; one renewal mechanism for all properties.
- All edge infra is captured in CDK alongside `compute_stack`, `data_stack`, `network_stack`, `observability_stack`.
- The vendor + product split lets a future second product ship under "Example" without re-onboarding through Atlassian.
- Reviewers and customers see `api.example.com` in network traffic, not an opaque App Runner hostname — small but real trust signal during security review.

### Negative / accepted trade-offs

- Three days of focused work to reach Marketplace-ready: ~1 day infra + repo find-and-replace, 1–2 days docs site build with Cruip templates.
- Amplify free tier expires after 12 months. Forecast cost: $1–5/mo at docs-site scale; well under the threshold for re-evaluation.
- Google Workspace adds one ~$7/month SaaS line item outside AWS billing. Acceptable trade for a real Gmail+Calendar+Drive client and for not maintaining a Lambda forwarder.
- Email deliverability depends on getting SPF, DKIM, and DMARC right on the first pass. Mitigated by sending a test email from each alias and inspecting Gmail's "Show original" view before publishing the support address on the Marketplace listing.

### Operational follow-ups

- Repo find-and-replace of the domain across the docs, `SECURITY.md`, and `backend/app/core/config.py` (delete dead `app_key` field, update `app_vendor_url`). One PR.
- One new CDK stack (a dedicated static-site stack) added to `infra/`, deployed via the existing `deploy.yml` OIDC path. The stack owns the `example.com` Route53 hosted zone, the Amplify app, the ACM cert, and the Google Workspace DNS records (MX/SPF/DKIM/DMARC/verification TXT).
- App Runner custom domain `api.example.com` added in the App Runner console; ACM validation records placed in Route53.
- Forge `manifest.yml` `remotes:` updated to `https://api.example.com`; `forge deploy --environment production` after the custom domain validates.
- Google Workspace tenant provisioned for `example.com`: one user, three aliases, DNS verified.
- Atlassian Marketplace listing form fields updated to your domain URLs and `support@` email at submission time.
