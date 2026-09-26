import { describe, it, expect } from "vitest";

import type { DrawerContent } from "./components/Drawer";
import type { DeferredEnvelope } from "./deferred";
import { createDrawerOpener } from "./drawerOpener";
import type { CaseRecord } from "./types";

function envelope(
  run = "01RUN",
  caseId = "c1",
): { $deferred: DeferredEnvelope } {
  return {
    $deferred: {
      preview: "the ans",
      size: 700_000,
      sha256: "ab".repeat(32),
      run,
      test: "tests/test_x.py::test_a",
      case: caseId,
      field: "output",
      attempt: 0,
    },
  };
}

function caseRecord(output: unknown): CaseRecord {
  return {
    inputs: "the input",
    expected_output: null,
    metadata: null,
    attempts: [{ outcome: "passed", output, results: {}, task_duration: 1 }],
    passed_attempts: 1,
    clean_attempts: 1,
    errored_attempts: 0,
    outcome: "passed",
  };
}

function casePane(output: unknown, title = "large"): DrawerContent {
  return {
    kind: "case",
    title,
    caseKey: title,
    caseResult: caseRecord(output),
  };
}

const plainPane: DrawerContent = {
  kind: "pair",
  title: "small",
  labelA: "A",
  labelB: "B",
  a: 1,
  b: 2,
};

/** A drawer that records what it shows, and fetches that the test resolves. */
function setup() {
  const shown: (DrawerContent | null)[] = [];
  const errors: string[] = [];
  const opener = createDrawerOpener({
    show: (content) => shown.push(content),
    onError: (message) => errors.push(message),
  });
  const pending: {
    env: DeferredEnvelope;
    resolve: (r: CaseRecord) => void;
    reject: (e: Error) => void;
  }[] = [];
  const fetchCase = (env: DeferredEnvelope) =>
    new Promise<CaseRecord>((resolve, reject) =>
      pending.push({ env, resolve, reject }),
    );
  const last = () => shown[shown.length - 1];
  return { opener, shown, errors, pending, fetchCase, last };
}

function outputOf(content: DrawerContent | null | undefined): unknown {
  if (content?.kind !== "case") return undefined;
  return content.caseResult.attempts[0]?.output;
}

describe("createDrawerOpener", () => {
  it("shows a loading pane, then the pane with the whole value", async () => {
    const { opener, pending, fetchCase, last } = setup();
    const opened = opener.open(casePane(envelope()), fetchCase);
    expect(last()?.kind).toBe("loading");
    pending[0]!.resolve(caseRecord("the whole answer"));
    await opened;
    expect(outputOf(last())).toBe("the whole answer");
  });

  it("leaves the content it was given unchanged", async () => {
    const { opener, pending, fetchCase } = setup();
    const content = casePane(envelope());
    const before = content.kind === "case" ? content.caseResult : null;
    const opened = opener.open(content, fetchCase);
    pending[0]!.resolve(caseRecord("the whole answer"));
    await opened;
    expect(content.kind === "case" && content.caseResult).toBe(before);
    expect(outputOf(content)).toEqual(envelope());
  });

  it("keeps the drawer closed when it closes before the fetch lands", async () => {
    const { opener, pending, fetchCase, last } = setup();
    const opened = opener.open(casePane(envelope()), fetchCase);
    opener.close();
    pending[0]!.resolve(caseRecord("late"));
    await opened;
    expect(last()).toBeNull();
  });

  it("keeps a plain pane that opened while the fetch was on its way", async () => {
    const { opener, pending, fetchCase, last } = setup();
    const opened = opener.open(casePane(envelope()), fetchCase);
    void opener.open(plainPane, fetchCase);
    pending[0]!.resolve(caseRecord("late"));
    await opened;
    expect(last()).toBe(plainPane);
  });

  it("keeps the newer of two large panes when the older lands last", async () => {
    const { opener, pending, fetchCase, last } = setup();
    const older = opener.open(
      casePane(envelope("01RUN", "c1"), "older"),
      fetchCase,
    );
    const newer = opener.open(
      casePane(envelope("01RUN", "c2"), "newer"),
      fetchCase,
    );
    pending[1]!.resolve(caseRecord("newer value"));
    await newer;
    pending[0]!.resolve(caseRecord("older value"));
    await older;
    expect(last()?.title).toBe("newer");
    expect(outputOf(last())).toBe("newer value");
  });

  it("fetches a case once, however often its pane opens", async () => {
    const { opener, pending, fetchCase, last } = setup();
    const first = opener.open(casePane(envelope()), fetchCase);
    pending[0]!.resolve(caseRecord("the whole answer"));
    await first;
    await opener.open(casePane(envelope()), fetchCase);
    expect(pending).toHaveLength(1);
    expect(outputOf(last())).toBe("the whole answer");
  });

  it("fetches a case again once its run was closed", async () => {
    const { opener, pending, fetchCase } = setup();
    const first = opener.open(casePane(envelope()), fetchCase);
    pending[0]!.resolve(caseRecord("the whole answer"));
    await first;
    opener.keepRuns(new Set(["01OTHER"]));
    void opener.open(casePane(envelope()), fetchCase);
    expect(pending).toHaveLength(2);
  });

  it("reports a failed fetch, unless the drawer moved on", async () => {
    const { opener, errors, pending, fetchCase, last } = setup();
    const failed = opener.open(casePane(envelope()), fetchCase);
    pending[0]!.reject(new Error("boom"));
    await failed;
    expect(last()).toBeNull();
    expect(errors).toEqual(["The values of this case did not load: boom"]);

    const superseded = opener.open(casePane(envelope()), fetchCase);
    void opener.open(plainPane, fetchCase);
    pending[1]!.reject(new Error("late"));
    await superseded;
    expect(last()).toBe(plainPane);
    expect(errors).toHaveLength(1);
  });
});
