import { StrictMode, useCallback, useState, type ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { Brand } from "./components/Brand";
import { Drawer, type DrawerContent } from "./components/Drawer";
import { RunDetail } from "./components/RunDetail";
import { RunDiff } from "./components/RunDiff";
import { ThemeToggle, initTheme } from "./components/ThemeToggle";
import {
  ReportDataError,
  readEmbeddedReport,
  type ReportData,
} from "./reportData";
import "./index.css";

/** The static report: one run, or one run against another, rendered from the
 *  data embedded in the page. Nothing here reaches a server, so the run view
 *  gets no repository actions and no way to navigate to another run. */
function Report({ data }: { data: ReportData }) {
  const [drawer, setDrawer] = useState<DrawerContent | null>(null);
  const closeDrawer = useCallback(() => setDrawer(null), []);
  const [attemptSel, setAttemptSel] = useState<Record<string, number>>({});
  const selectAttempt = useCallback((key: string, index: number) => {
    setAttemptSel((m) => ({ ...m, [key]: index }));
  }, []);
  // Base and compare as the CLI set them, until the reader swaps them.
  const [swapped, setSwapped] = useState(false);
  const swap = useCallback(() => setSwapped((s) => !s), []);

  const generated = data.generated_at
    ? new Date(data.generated_at).toLocaleString()
    : null;
  const sides = data.against
    ? swapped
      ? {
          a: data.run,
          b: data.against,
          viaA: data.via,
          viaB: data.against_via,
        }
      : {
          a: data.against,
          b: data.run,
          viaA: data.against_via,
          viaB: data.via,
        }
    : null;
  return (
    <div className="report">
      <header className="report-bar">
        <Brand />
        <p className="report-meta">
          {generated && <>report generated {generated}</>}
          {generated && data.generated_by && " · "}
          {data.generated_by && (
            <>
              evaltrack <code>{data.generated_by}</code>
            </>
          )}
        </p>
        <ThemeToggle />
      </header>
      <main className="main">
        {data.history_error && (
          <div className="notice-banner" role="alert">
            <span className="notice-text">
              This report has no reliability history: the mainline could not be
              read when it was written ({data.history_error}).
            </span>
          </div>
        )}
        {sides ? (
          <RunDiff
            a={sides.a}
            b={sides.b}
            viaA={sides.viaA ?? undefined}
            viaB={sides.viaB ?? undefined}
            standalone
            onSwap={swap}
            onOpenDrawer={setDrawer}
          />
        ) : (
          <RunDetail
            run={data.run}
            via={data.via ?? undefined}
            refs={data.refs}
            standalone
            history={
              data.history_error
                ? { status: "error" }
                : { status: "ready", data: data.history }
            }
            mainline={data.mainline}
            prUrlTemplate={data.pr_url_template}
            onOpenDrawer={setDrawer}
            attemptSel={attemptSel}
            onSelectAttempt={selectAttempt}
          />
        )}
      </main>
      <Drawer
        content={drawer}
        onClose={closeDrawer}
        attemptSel={attemptSel}
        onSelectAttempt={selectAttempt}
      />
    </div>
  );
}

function ReportUnreadable({ message }: { message: string }) {
  return (
    <div className="report">
      <header className="report-bar">
        <Brand />
        <ThemeToggle />
      </header>
      <main className="main">
        <div className="empty-state" role="alert">
          {message}
        </div>
      </main>
    </div>
  );
}

initTheme();

const root = document.getElementById("root");
if (!root) throw new Error("missing #root in report.html");

let page: ReactElement;
try {
  const data = readEmbeddedReport(document);
  document.title = `evaltrack report · ${data.via ?? data.run.id}`;
  page = <Report data={data} />;
} catch (e) {
  if (!(e instanceof ReportDataError)) throw e;
  page = <ReportUnreadable message={e.message} />;
}
createRoot(root).render(<StrictMode>{page}</StrictMode>);
