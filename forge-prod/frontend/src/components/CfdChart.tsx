// Cumulative Flow Diagram.
//
// Stacked area chart over the last 30 days. One band per status. Wider
// bands and bands that bulge over time = bottlenecks accumulating.

import { AxisBottom, AxisLeft } from "@visx/axis";
import { Group } from "@visx/group";
import { scaleLinear, scaleOrdinal, scaleTime } from "@visx/scale";
import { AreaStack } from "@visx/shape";
import { useTooltip, useTooltipInPortal, defaultStyles as tooltipDefaultStyles } from "@visx/tooltip";
import { useMemo } from "react";

import type { CfdResponse } from "../lib/types";

interface Props {
  data: CfdResponse;
  windowLabel?: string;
  width?: number;
  height?: number;
}

const PALETTE = [
  "#2563eb", "#0891b2", "#16a34a", "#65a30d", "#d97706",
  "#dc2626", "#db2777", "#7c3aed", "#9333ea", "#0f766e",
  "#475569", "#64748b", "#94a3b8",
];

interface Row {
  date: Date;
  total: number;
  // status -> count
  [status: string]: number | Date;
}

export function CfdChart({ data, windowLabel, width = 900, height = 380 }: Props) {
  const { containerRef, TooltipInPortal } = useTooltipInPortal({
    detectBounds: true,
    scroll: true,
  });
  const { tooltipOpen, tooltipLeft, tooltipTop, tooltipData, showTooltip, hideTooltip } =
    useTooltip<{ date: Date; row: Record<string, number> }>();

  // Order statuses by total volume desc so the biggest bands sit at the
  // bottom — easier to compare growth across time.
  const orderedStatuses = useMemo(() => {
    const totals = new Map<string, number>();
    for (const day of data.days) {
      for (const [s, n] of Object.entries(day.by_status)) {
        totals.set(s, (totals.get(s) ?? 0) + n);
      }
    }
    return [...totals.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([s]) => s);
  }, [data]);

  const rows: Row[] = useMemo(() => {
    return data.days.map((d) => {
      const row: Row = { date: new Date(`${d.date}T23:59:59Z`), total: 0 };
      let total = 0;
      for (const s of orderedStatuses) {
        const v = d.by_status[s] ?? 0;
        row[s] = v;
        total += v;
      }
      row.total = total;
      return row;
    });
  }, [data.days, orderedStatuses]);

  const margin = { top: 16, right: 24, bottom: 40, left: 56 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const xScale = scaleTime<number>({
    domain: rows.length > 0 ? [rows[0].date, rows[rows.length - 1].date] : [new Date(), new Date()],
    range: [0, innerW],
  });
  const yScale = scaleLinear<number>({
    domain: [0, Math.max(...rows.map((r) => r.total), 1) * 1.05],
    range: [innerH, 0],
    nice: true,
  });
  const colorScale = scaleOrdinal<string, string>({
    domain: orderedStatuses,
    range: orderedStatuses.map((_, i) => PALETTE[i % PALETTE.length]),
  });

  if (data.days.length === 0 || orderedStatuses.length === 0) {
    return (
      <section className="rounded-2xl border border-ink-200 bg-white p-6 text-ink-600 dark:border-ink-800 dark:bg-ink-900 dark:text-ink-400">
        <p className="text-sm uppercase tracking-wide text-ink-400">Cumulative Flow</p>
        <p className="mt-2">No flow data in the window.</p>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-ink-200 bg-white p-6 shadow-sm dark:border-ink-800 dark:bg-ink-900">
      <header className="mb-4">
        <p className="text-sm uppercase tracking-wide text-ink-400">Cumulative Flow</p>
        <h3 className="mt-1 text-xl font-semibold">
          Tickets by status — {windowLabel ?? `last ${data.days.length} days`}
        </h3>
        <p className="mt-1 text-xs text-ink-400">
          Bands widening over time = work piling up faster than it leaves. Hover to see the
          exact distribution on a given day.
        </p>
      </header>
      <div ref={containerRef} className="relative">
        <svg width={width} height={height}>
          <Group left={margin.left} top={margin.top}>
            <AreaStack<Row>
              keys={orderedStatuses}
              data={rows}
              x={(d) => xScale(d.data.date) ?? 0}
              y0={(d) => yScale(d[0]) ?? 0}
              y1={(d) => yScale(d[1]) ?? 0}
            >
              {({ stacks, path }) =>
                stacks.map((stack) => (
                  <path
                    key={`stack-${stack.key}`}
                    d={path(stack) || ""}
                    stroke="transparent"
                    fill={colorScale(String(stack.key))}
                    fillOpacity={0.85}
                    onMouseMove={(e) => {
                      const rect = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
                      const cx = e.clientX - rect.left - margin.left;
                      // Find the row whose date is closest to cx.
                      const xVal = xScale.invert(cx);
                      let nearest = rows[0];
                      let nearestDiff = Math.abs(rows[0].date.getTime() - xVal.getTime());
                      for (const r of rows) {
                        const diff = Math.abs(r.date.getTime() - xVal.getTime());
                        if (diff < nearestDiff) {
                          nearestDiff = diff;
                          nearest = r;
                        }
                      }
                      const row: Record<string, number> = {};
                      for (const s of orderedStatuses) row[s] = nearest[s] as number;
                      showTooltip({
                        tooltipData: { date: nearest.date, row },
                        tooltipLeft: e.clientX - rect.left,
                        tooltipTop: e.clientY - rect.top,
                      });
                    }}
                    onMouseLeave={hideTooltip}
                  />
                ))
              }
            </AreaStack>
            <AxisLeft
              scale={yScale}
              stroke="#94a3b8"
              tickStroke="#94a3b8"
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
            <div style={{ fontWeight: 600 }}>{tooltipData.date.toISOString().slice(0, 10)}</div>
            <div style={{ marginTop: 4 }}>
              {orderedStatuses
                .filter((s) => tooltipData.row[s] > 0)
                .map((s) => (
                  <div key={s} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                    <span>
                      <span
                        style={{
                          display: "inline-block",
                          width: 8,
                          height: 8,
                          background: colorScale(s),
                          marginRight: 6,
                          borderRadius: 2,
                        }}
                      />
                      {s}
                    </span>
                    <span style={{ opacity: 0.85 }}>{tooltipData.row[s]}</span>
                  </div>
                ))}
            </div>
          </TooltipInPortal>
        )}
      </div>
    </section>
  );
}
