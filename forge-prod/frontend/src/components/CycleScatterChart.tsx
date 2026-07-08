// Cycle Time Scatter.
//
// One dot per completed ticket: X = completion date, Y = cycle days.
// Dashed overlay lines at P50/P85/P95 of the same set. Outliers above
// P95 are visible candidates for review.

import { router } from "@forge/bridge";
import { AxisBottom, AxisLeft } from "@visx/axis";
import { Group } from "@visx/group";
import { scaleLinear, scaleTime } from "@visx/scale";
import { Circle, Line } from "@visx/shape";
import { useTooltip, useTooltipInPortal, defaultStyles as tooltipDefaultStyles } from "@visx/tooltip";
import { useMemo } from "react";

import type { CycleScatterResponse, ScatterPoint } from "../lib/types";

interface Props {
  data: CycleScatterResponse;
  cloudHostname?: string;
  windowLabel?: string;
  width?: number;
  height?: number;
}

const TYPE_COLORS: Record<string, string> = {
  Story: "#2563eb",
  Bug: "#dc2626",
  Task: "#16a34a",
  Epic: "#7c3aed",
  "Sub-task": "#0891b2",
};
const DEFAULT_COLOR = "#475569";

function colorOf(t: ScatterPoint): string {
  if (t.issue_type && TYPE_COLORS[t.issue_type]) return TYPE_COLORS[t.issue_type];
  return DEFAULT_COLOR;
}

export function CycleScatterChart({
  data,
  cloudHostname,
  windowLabel,
  width = 900,
  height = 380,
}: Props) {
  const { containerRef, TooltipInPortal } = useTooltipInPortal({
    detectBounds: true,
    scroll: true,
  });
  const { tooltipOpen, tooltipLeft, tooltipTop, tooltipData, showTooltip, hideTooltip } =
    useTooltip<ScatterPoint>();

  const points = useMemo(
    () =>
      data.points.map((p) => ({
        ...p,
        date: new Date(p.completed_at),
      })),
    [data.points],
  );

  const margin = { top: 16, right: 24, bottom: 40, left: 56 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const xDomain = useMemo<[Date, Date]>(() => {
    if (points.length === 0) return [new Date(data.window_start), new Date(data.window_end)];
    return [
      new Date(Math.min(...points.map((p) => p.date.getTime()))),
      new Date(Math.max(...points.map((p) => p.date.getTime()))),
    ];
  }, [points, data.window_start, data.window_end]);

  const maxY = Math.max(
    1,
    Math.max(...points.map((p) => p.cycle_days), 0) * 1.1,
    data.p95_cycle_days ?? 0,
  );

  const xScale = scaleTime<number>({ domain: xDomain, range: [0, innerW] });
  const yScale = scaleLinear<number>({ domain: [0, maxY], range: [innerH, 0], nice: true });

  function openInJira(t: ScatterPoint) {
    if (!cloudHostname) return;
    void router.open(`https://${cloudHostname}/browse/${t.key}`);
  }

  if (points.length === 0) {
    return (
      <section className="rounded-2xl border border-ink-200 bg-white p-6 text-ink-600 dark:border-ink-800 dark:bg-ink-900 dark:text-ink-400">
        <p className="text-sm uppercase tracking-wide text-ink-400">Cycle Time Scatter</p>
        <p className="mt-2">No completed tickets in the window.</p>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
      <header className="mb-4">
        <p className="text-sm uppercase tracking-wide text-ink-400">Cycle Time Scatter</p>
        <h3 className="mt-1 text-xl font-semibold">
          {points.length} completed ticket{points.length === 1 ? "" : "s"} ·{" "}
          {windowLabel ??
            `last ${Math.round(
              (new Date(data.window_end).getTime() -
                new Date(data.window_start).getTime()) /
                86_400_000,
            )} days`}
        </h3>
        <p className="mt-1 text-xs text-ink-400">
          Each dot = one completed ticket. Y axis = days from creation to done. Dashed lines:
          {data.p50_cycle_days !== null && <> P50 ({data.p50_cycle_days.toFixed(1)}d)</>}
          {data.p85_cycle_days !== null && <> · P85 ({data.p85_cycle_days.toFixed(1)}d)</>}
          {data.p95_cycle_days !== null && <> · P95 ({data.p95_cycle_days.toFixed(1)}d)</>}
          {". "}Color = issue type.
        </p>
      </header>
      <div ref={containerRef} className="relative">
        <svg width={width} height={height}>
          <Group left={margin.left} top={margin.top}>
            {(
              [
                ["#94a3b8", data.p50_cycle_days, "P50"],
                ["#f59e0b", data.p85_cycle_days, "P85"],
                ["#dc2626", data.p95_cycle_days, "P95"],
              ] as const
            ).map(([color, value, label]) =>
              value !== null && value <= maxY ? (
                <Line
                  key={label}
                  from={{ x: 0, y: yScale(value) }}
                  to={{ x: innerW, y: yScale(value) }}
                  stroke={color}
                  strokeOpacity={0.55}
                  strokeDasharray="6 4"
                  strokeWidth={1.25}
                />
              ) : null,
            )}
            {points.map((p) => (
              <Circle
                key={p.key}
                cx={xScale(p.date)}
                cy={yScale(p.cycle_days)}
                r={5}
                fill={colorOf(p)}
                fillOpacity={0.7}
                stroke={colorOf(p)}
                strokeWidth={1}
                onMouseMove={(e) => {
                  const rect = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                  showTooltip({
                    tooltipData: p,
                    tooltipLeft: e.clientX - rect.left,
                    tooltipTop: e.clientY - rect.top,
                  });
                }}
                onMouseLeave={hideTooltip}
                onClick={() => openInJira(p)}
                style={{ cursor: cloudHostname ? "pointer" : "default" }}
              />
            ))}
            <AxisLeft
              scale={yScale}
              stroke="#94a3b8"
              tickStroke="#94a3b8"
              tickFormat={(d) => `${d}d`}
              tickLabelProps={() => ({
                fill: "#475569",
                fontSize: 11,
                textAnchor: "end",
                dy: "0.33em",
                dx: "-0.25em",
              })}
            />
            <AxisBottom
              top={innerH}
              scale={xScale}
              stroke="#94a3b8"
              tickStroke="#94a3b8"
              numTicks={6}
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
              boxShadow: "0 4px 12px rgba(0,0,0,0.25)",
            }}
          >
            <div style={{ fontWeight: 600 }}>
              {tooltipData.key}
              <span style={{ marginLeft: 8, opacity: 0.7 }}>
                · {tooltipData.cycle_days.toFixed(1)}d
              </span>
            </div>
            {tooltipData.summary && (
              <div style={{ marginTop: 2, opacity: 0.85 }}>{tooltipData.summary}</div>
            )}
            <div style={{ marginTop: 4, opacity: 0.7 }}>
              {tooltipData.issue_type || "—"}
              {tooltipData.priority && <> · {tooltipData.priority}</>}
              {tooltipData.assignee && <> · {tooltipData.assignee}</>}
            </div>
            {cloudHostname && (
              <div style={{ marginTop: 6, fontSize: 10, opacity: 0.6 }}>Click to open in Jira</div>
            )}
          </TooltipInPortal>
        )}
      </div>
    </section>
  );
}
