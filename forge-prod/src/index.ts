import { dailyAlertEvalResolver, hourlyAlertEvalResolver } from "./resolvers/alerts";
import { backfillConsumer } from "./resolvers/backfill";
import { dashboardResolver } from "./resolvers/dashboard";
import { issuePanelResolver } from "./resolvers/issuePanel";
import {
  installLifecycleResolver,
  lifecycleResolver,
} from "./resolvers/lifecycle";
import { personalDataReportingResolver } from "./resolvers/personal-data";
import { recomputeConsumer, startRecompute } from "./resolvers/recompute";
import {
  issueDeletedResolver,
  issueWebhookResolver,
  reconcileResolver,
} from "./resolvers/webhooks";

export {
  backfillConsumer,
  dailyAlertEvalResolver,
  dashboardResolver,
  hourlyAlertEvalResolver,
  installLifecycleResolver,
  issueDeletedResolver,
  issuePanelResolver,
  issueWebhookResolver,
  lifecycleResolver,
  personalDataReportingResolver,
  reconcileResolver,
  recomputeConsumer,
  startRecompute,
};
