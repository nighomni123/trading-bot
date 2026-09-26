import type { Metadata } from "next";
import "./globals.css";
import TopBar from "@/components/TopBar";
import Sidebar from "@/components/Sidebar";
import NotTrading from "@/components/NotTrading";
import { buildNotTrading, buildVintage } from "@/lib/summary";
import { loadStage12Coverage, loadCostMeasurement } from "@/lib/data";

export const metadata: Metadata = {
  title: "Epistemic Quant Dashboard",
  description:
    "Research telemetry for the BTCUSDT perpetual research engine. Research mode — not trading.",
};

export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const nt = buildNotTrading();
  const { vintage, note } = buildVintage();
  const cov = loadStage12Coverage();

  // Pipeline health = the worst node state. Economic gate governs.
  const health = nt.signalBps < nt.costBps
    ? { label: "ECON GATE FAIL", tone: "fail" as const }
    : { label: "ECON GATE OPEN", tone: "pass" as const };

  return (
    <html lang="en">
      <head>
        {/* Progressive enhancement: no build-time font fetch, solid fallbacks. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap"
        />
      </head>
      <body className="min-h-screen">
        <div className="flex min-h-screen flex-col">
          <TopBar
            vintage={vintage}
            vintageNote={
              note ? `${note}${cov ? ` · ${cov.rows.toLocaleString("en-US")} rows` : ""}` : undefined
            }
            health={health}
          />
          <div className="flex flex-1 min-h-0">
            <Sidebar
              measuredRt={nt.costBps}
              configuredRt={loadCostMeasurement()?.profiles?.find((p) => p.name === "configured_taker_taker")?.total_rt_bps ?? 15}
            />
            <main className="min-w-0 flex-1 px-4 py-4">
              <div className="mx-auto mb-4 max-w-[1500px]">
                <NotTrading d={nt} />
              </div>
              <div className="mx-auto max-w-[1500px]">{children}</div>
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
