import { StrictMode, useCallback, useState, type ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { Brand } from "./components/Brand";
import { Drawer, type DrawerContent } from "./components/Drawer";
import { RunDetail } from "./components/RunDetail";
import { RunDiff } from "./components/RunDiff";
import { ThemeToggle, initTheme } from "./components/ThemeToggle";
import { collectDeferred, deferredBytes } from "./deferred";
import { formatBytes, formatPlural } from "./format";
import {
  ReportDataError,
  countCasesWithValuesLeftOut,
  readEmbeddedReport,
  showPreviewsOfValuesLeftOut,
  type ReportData,
} from "./reportData";
import "./index.css";

/** The static report: one run, with the comparison against the mainline one
 *  click away, rendered from the data embedded in the page. Nothing here
 *  reaches a server, so the run view gets no repository actions and no way to
 *  navigate to another run. */
function Report({ data }: { data: ReportData }) {
  const [drawer, setDrawer] = useState<DrawerContent | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // The notice is about the open pane, so it goes when the pane does.
  const closeDrawer = useCallback(() => {
    setDrawer(null);
    setNotice(null);
  }, []);
  const dismissNotice = useCallback(() => setNotice(null), []);
  // A value the report left out has nowhere to be fetched from. The pane
  // opens anyway, since the rest of it is there, with the first characters
  // standing in for the value and a banner saying so.
  const openDrawer = useCallback((content: DrawerContent) => {
    const deferred = collectDeferred(content);
    const first = deferred[0];
    setNotice(
      first
        ? `This pane shows only the first characters of ${formatBytes(deferredBytes(deferred))} ` +
            `that the report left out. Open run ${first.run} in the dashboard to see it whole. ` +
            "`evaltrack report --full` writes a report with every value in it."
        : null,
    );
    setDrawer(showPreviewsOfValuesLeftOut(content));
  }, []);
  // So the bar can say the page is not whole.
  const leftOut = countCasesWithValuesLeftOut(data);
  const [attemptSel, setAttemptSel] = useState<Record<string, number>>({});
  const selectAttempt = useCallback((key: string, index: number) => {
    setAttemptSel((m) => ({ ...m, [key]: index }));
  }, []);
  // The page opens on the run, as the dashboard does. A pane belongs to the
  // view it was opened from, so it closes when the view changes.
  const [comparing, setComparing] = useState(false);
  const compare = useCallback(() => {
    closeDrawer();
    setComparing(true);
  }, [closeDrawer]);
  const back = useCallback(() => {
    closeDrawer();
    setComparing(false);
  }, [closeDrawer]);
  // The baseline is the base and the run the compare, until the reader swaps.
  const [swapped, setSwapped] = useState(false);
  const swap = useCallback(() => setSwapped((s) => !s), []);

  const generated = new Date(data.generated_at).toLocaleString();
  const reported = { run: data.run.run, via: data.run.via ?? undefined };
  const baseline = data.baseline
    ? { run: data.baseline, via: "baseline" }
    : null;
  const sides =
    comparing && baseline
      ? swapped
        ? { a: reported, b: baseline }
        : { a: baseline, b: reported }
      : null;
  return (
    <div className="report">
      <header className="report-bar">
        <Brand />
        <p className="report-meta">
          report generated {generated} · evaltrack{" "}
          <code>{data.generated_by}</code>
          {leftOut > 0 && (
            <>
              {" · "}
              {formatPlural(leftOut, "case")} with a large value left out
            </>
          )}
        </p>
        <ThemeToggle />
      </header>
      <main className="main">
        {notice && (
          <div className="notice-banner" role="alert">
            <span className="notice-text">{notice}</span>
            <button
              type="button"
              className="notice-dismiss"
              onClick={dismissNotice}
              aria-label="dismiss"
            >
              ×
            </button>
          </div>
        )}
        {data.mainline_error && (
          <div className="notice-banner" role="alert">
            <span className="notice-text">
              This report has no history and no comparison against the mainline,
              which could not be read when the report was written (
              {data.mainline_error}).
            </span>
          </div>
        )}
        {sides ? (
          <RunDiff
            a={sides.a.run}
            b={sides.b.run}
            viaA={sides.a.via}
            viaB={sides.b.via}
            onBack={back}
            onSwap={swap}
            onOpenDrawer={openDrawer}
          />
        ) : (
          <RunDetail
            run={reported.run}
            via={reported.via}
            refs={data.refs}
            history={
              data.mainline_error
                ? { status: "error" }
                : { status: "ready", data: data.history }
            }
            mainline={data.mainline}
            prUrlTemplate={data.pr_url_template}
            onCompareToBaseline={baseline ? compare : undefined}
            onOpenDrawer={openDrawer}
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
  document.title = `evaltrack report · ${data.run.via ?? data.run.run.id}`;
  page = <Report data={data} />;
} catch (e) {
  if (!(e instanceof ReportDataError)) throw e;
  page = <ReportUnreadable message={e.message} />;
}
createRoot(root).render(<StrictMode>{page}</StrictMode>);
