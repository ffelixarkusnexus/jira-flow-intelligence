// WIP Aging bubble chart.
//
// One bubble per in-flight ticket. X = days in current status. Y = status.
// Size = story points (or constant fallback). Color = assignee group.
// Overlay vertical line at the tenant's P95 cycle time — bubbles to the
// right of that line are aging past normal flow ("stuck").
//
// Built on Visx primitives so we keep the bundle small and have full
// control over the rendering. Hover shows a tooltip; click opens the
// ticket in Jira via @forge/bridge router.open().

import { router } from "@forge/bridge";
import { AxisBottom, AxisLeft } from "@visx/axis";
import { Group } from "@visx/group";
import { scaleBand, scaleLinear, scaleOrdinal } from "@visx/scale";
import { Circle, Line } from "@visx/shape";
import { useTooltip, useTooltipInPortal, defaultStyles as tooltipDefaultStyles } from "@visx/tooltip";
import { useMemo, useState } from "react";

import type { WipAgingResponse, WipAgingTicket } from "../lib/types";

interface Props {
  data: WipAgingResponse;
  cloudHostname?: string;
  width?: number;
  height?: number;
}

const COLORS = [
  "#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed",
  "#0891b2", "#db2777", "#65a30d", "#9333ea", "#ea580c",
];
const UNASSIGNED_COLOR = "#94a3b8";

// Map Jira's standard priority names to a numeric weight when story points
// are missing. Highest=8 / High=5 / Medium=3 / Low=2 / Lowest=1 mirrors
// how teams typically weight effort when they don't formally estimate.
const PRIORITY_WEIGHT: Record<string, number> = {
  Highest: 8,
  High: 5,
  Medium: 3,
  Low: 2,
  Lowest: 1,
};

// Bubble radius (px). Story points first; priority fallback when teams
// don't estimate; constant default if neither is set.
function bubbleRadius(sp: number | null, priority: string | null): number {
  const fromSp = sp !== null && sp > 0 ? sp : null;
  const fromPri =
    priority !== null && PRIORITY_WEIGHT[priority] !== undefined
      ? PRIORITY_WEIGHT[priority]
      : null;
  const weight = fromSp ?? fromPri;
  if (weight === null) return 6;
  // Sqrt scaling so a 5-point ticket isn't 5x bigger than a 1-pointer in
  // visual area. Floor 6, ceiling 22.
  return Math.min(22, Math.max(6, 5 + Math.sqrt(weight) * 2));
}

export function WipAgingChart({
  data,
  cloudHostname,
  width = 900,
  height = 520,
}: Props) {
  const { containerRef, TooltipInPortal } = useTooltipInPortal({
    detectBounds: true,
    scroll: true,
  });
  const {
    tooltipOpen,
    tooltipLeft,
    tooltipTop,
    tooltipData,
    showTooltip,
    hideTooltip,
  } = useTooltip<WipAgingTicket>();

  const [assigneeFilter, setAssigneeFilter] = useState<string | null>(null);

  const tickets = useMemo(() => {
    if (!assigneeFilter) return data.tickets;
    return data.tickets.filter(
      (t) => (t.assignee || "Unassigned") === assigneeFilter,
    );
  }, [data.tickets, assigneeFilter]);

  // Y axis: distinct statuses, ordered by max age desc so the most
  // problematic stage sits at the top.
  const statusesInOrder = useMemo(() => {
    const maxAgeByStatus = new Map<string, number>();
    for (const t of data.tickets) {
      const cur = maxAgeByStatus.get(t.status) ?? 0;
      if (t.days_in_status > cur) maxAgeByStatus.set(t.status, t.days_in_status);
    }
    return [...maxAgeByStatus.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([s]) => s);
  }, [data.tickets]);

  const assignees = useMemo(() => {
    const seen = new Set<string>();
    for (const t of data.tickets) seen.add(t.assignee || "Unassigned");
    return [...seen].sort();
  }, [data.tickets]);

  const margin = { top: 16, right: 24, bottom: 56, left: 200 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const maxDays = Math.max(
    14, // minimum sensible right edge so a small dataset doesn't squish bubbles
    Math.ceil(Math.max(...data.tickets.map((t) => t.days_in_status), 0) * 1.05),
    data.p95_cycle_days ?? 0,
  );

  const xScale = scaleLinear<number>({
    domain: [0, maxDays],
    range: [0, innerW],
  });

  const yScale = scaleBand<string>({
    domain: statusesInOrder.length > 0 ? statusesInOrder : ["(no in-flight tickets)"],
    range: [0, innerH],
    padding: 0.2,
  });

  const colorScale = scaleOrdinal<string, string>({
    domain: assignees,
    range: assignees.map((a) => (a === "Unassigned" ? UNASSIGNED_COLOR : COLORS[0])),
  });
  // Re-assign deterministic colors so each non-unassigned assignee gets a
  // distinct one (scaleOrdinal cycles, but we want the "Unassigned" exception).
  const assigneeColors = new Map<string, string>();
  let colorIdx = 0;
  for (const a of assignees) {
    if (a === "Unassigned") {
      assigneeColors.set(a, UNASSIGNED_COLOR);
    } else {
      assigneeColors.set(a, COLORS[colorIdx % COLORS.length]);
      colorIdx += 1;
    }
  }
  void colorScale; // keep for future legend integration

  function colorOf(t: WipAgingTicket): string {
    return assigneeColors.get(t.assignee || "Unassigned") || UNASSIGNED_COLOR;
  }

  function openInJira(t: WipAgingTicket) {
    if (!cloudHostname) return;
    void router.open(`https://${cloudHostname}/browse/${t.key}`);
  }

  if (data.tickets.length === 0) {
    return (
      <section className="rounded-2xl border border-ink-200 bg-white p-6 text-ink-600 dark:border-ink-800 dark:bg-ink-900 dark:text-ink-400">
        <p className="text-sm uppercase tracking-wide text-ink-400">WIP Aging</p>
        <p className="mt-2">No in-flight tickets to plot.</p>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
      <header className="mb-4 flex items-baseline justify-between gap-4 flex-wrap">
        <div>
          <p className="text-sm uppercase tracking-wide text-ink-400">WIP Aging</p>
          <h3 className="mt-1 text-xl font-semibold">
            {tickets.length} in-flight ticket{tickets.length === 1 ? "" : "s"} by stage
          </h3>
          <p className="mt-1 text-xs text-ink-400">
            Snapshot of work in flight right now — independent of the window picker
            above. X = days in current status. Bubble size = story points
            (priority as fallback). Color = assignee.
            {data.p95_cycle_days !== null && (
              <>
                {" "}Dashed line at P95 cycle ({data.p95_cycle_days.toFixed(1)} days, n=
                {data.sample_size}) — bubbles to the right are aging past normal flow.
              </>
            )}
          </p>
        </div>
        <select
          aria-label="Assignee filter"
          value={assigneeFilter ?? ""}
          onChange={(e) => setAssigneeFilter(e.target.value || null)}
          className="rounded-md border border-ink-200 bg-white px-2 py-1 text-xs text-ink-800 dark:border-ink-800 dark:bg-ink-900 dark:text-ink-100"
        >
          <option value="">All assignees</option>
          {assignees.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
      </header>

      <div ref={containerRef} className="relative">
        <svg width={width} height={height}>
          <Group left={margin.left} top={margin.top}>
            {data.p95_cycle_days !== null && data.p95_cycle_days <= maxDays && (
              <Line
                from={{ x: xScale(data.p95_cycle_days), y: 0 }}
                to={{ x: xScale(data.p95_cycle_days), y: innerH }}
                stroke="#dc2626"
                strokeOpacity={0.5}
                strokeDasharray="6 4"
                strokeWidth={1.5}
              />
            )}
            {tickets.map((t) => (
              <Circle
                key={t.key}
                cx={xScale(t.days_in_status)}
                cy={(yScale(t.status) ?? 0) + yScale.bandwidth() / 2}
                r={bubbleRadius(t.story_points, t.priority)}
                fill={colorOf(t)}
                fillOpacity={0.55}
                stroke={colorOf(t)}
                strokeWidth={1.5}
                onMouseMove={(e) => {
                  const rect = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                  showTooltip({
                    tooltipData: t,
                    tooltipLeft: e.clientX - rect.left,
                    tooltipTop: e.clientY - rect.top,
                  });
                }}
                onMouseLeave={hideTooltip}
                onClick={() => openInJira(t)}
                style={{ cursor: cloudHostname ? "pointer" : "default" }}
              />
            ))}
            <AxisLeft
              scale={yScale}
              stroke="#94a3b8"
              tickStroke="#94a3b8"
              tickLabelProps={() => ({
                fill: "#475569",
                fontSize: 11,
                textAnchor: "end",
                dy: "0.33em",
                dx: "-0.5em",
              })}
            />
            <AxisBottom
              top={innerH}
              scale={xScale}
              stroke="#94a3b8"
              tickStroke="#94a3b8"
              numTicks={Math.min(8, Math.max(4, Math.floor(maxDays / 5)))}
              tickFormat={(d) => `${d}d`}
              tickLabelProps={() => ({
                fill: "#475569",
                fontSize: 11,
                textAnchor: "middle",
                dy: "0.33em",
              })}
            />
          </Group>
        </svg>
        {tooltipOpen && tooltipData && (
          <TooltipInPortal
            top={tooltipTop}
            left={tooltipLeft}
            style={{
              ...tooltipDefaultStyles,
              background: "#0f172a",
              color: "#f1f5f9",
              padding: "8px 12px",
              borderRadius: 6,
              fontSize: 12,
              maxWidth: 280,
              lineHeight: 1.4,
              boxShadow: "0 4px 12px rgba(0,0,0,0.25)",
            }}
          >
            <div style={{ fontWeight: 600 }}>
              {tooltipData.key}
              {tooltipData.story_points !== null && (
                <span style={{ marginLeft: 8, opacity: 0.7 }}>
                  · {tooltipData.story_points} pts
                </span>
              )}
            </div>
            {tooltipData.summary && (
              <div style={{ marginTop: 2, opacity: 0.85 }}>{tooltipData.summary}</div>
            )}
            <div style={{ marginTop: 6, opacity: 0.7 }}>
              {tooltipData.status} · {tooltipData.days_in_status.toFixed(1)}d in status ·
              {" "}{tooltipData.cycle_days.toFixed(1)}d total
            </div>
            <div style={{ marginTop: 2, opacity: 0.7 }}>
              {tooltipData.assignee || "Unassigned"}
              {tooltipData.priority && <> · {tooltipData.priority}</>}
            </div>
            {cloudHostname && (
              <div style={{ marginTop: 6, fontSize: 10, opacity: 0.6 }}>
                Click to open in Jira
              </div>
            )}
          </TooltipInPortal>
        )}
      </div>
    </section>
  );
}
