// ADR-0044: Issue View Panel resolver. Pure pass-through — extracts the
// issue key from Forge context and proxies to the backend's
// /api/forge/issue/{key}/panel-data endpoint. The backend computes the
// response shape from existing time_slices data; this resolver does no
// computation of its own.

import { invokeRemote } from "@forge/api";
import Resolver from "@forge/resolver";

const BACKEND_REMOTE_KEY = "backend";

interface IssuePanelContext {
  extension?: {
    issue?: {
      key?: string;
    };
  };
}

const resolver = new Resolver();

resolver.define("getIssueData", async ({ context }: { context: IssuePanelContext }) => {
  const issueKey = context.extension?.issue?.key;
  if (!issueKey) {
    return {
      error: "missing-issue-key",
      message: "Forge context did not include extension.issue.key. Cannot render panel.",
    };
  }
  const res = await invokeRemote(BACKEND_REMOTE_KEY, {
    method: "GET",
    path: `/api/forge/issue/${encodeURIComponent(issueKey)}/panel-data`,
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    return {
      error: "backend-error",
      status: res.status,
      message: await res.text(),
    };
  }
  return res.json();
});

export const issuePanelResolver = resolver.getDefinitions();
