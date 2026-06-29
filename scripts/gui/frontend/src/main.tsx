import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import { FileManager } from "@cubone/react-file-manager";
import "@cubone/react-file-manager/dist/style.css";
import "./styles.css";
import {
  ActionName,
  AutomationActionName,
  AutomationSettings,
  DashboardData,
  FileItem,
  JobSummary,
  PurchaseDocType,
  PurchaseDocsData,
  PurchaseImageHelperData,
  SessionInfo,
  createFolder,
  deletePaths,
  deletePurchaseImage,
  downloadUrl,
  listFiles,
  loadJobLog,
  loadJobs,
  loadPurchaseDocs,
  loadPurchaseImageHelper,
  loadDashboard,
  loadSession,
  loadAutomationSettings,
  loadProjects,
  movePaths,
  previewUrl,
  renamePath,
  reorderPurchaseImages,
  saveAutomationSettings,
  startAction,
  updatePurchaseProject,
  updatePurchaseDocType,
  uploadFiles,
  uploadPurchaseDocs,
  uploadPurchaseImages,
} from "./api";

type ManagerFile = FileItem;
type View = "dashboard" | "files" | "jobs" | "images" | "settings";

const UPLOAD_EXTENSIONS = new Set([
  ".pdf",
  ".docx",
  ".jpg",
  ".jpeg",
  ".png",
  ".webp",
  ".bmp",
  ".tif",
  ".tiff",
  ".xls",
  ".xlsx",
  ".hwp",
  ".hwpx",
  ".csv",
  ".json",
  ".txt",
]);

const PURCHASE_DOC_OPTIONS: Array<{ value: PurchaseDocType; label: string }> = [
  { value: "unknown", label: "기타/미분류" },
  { value: "estimate", label: "견적서" },
  { value: "statement", label: "거래명세서" },
  { value: "tax_invoice", label: "전자세금계산서" },
  { value: "business_registration", label: "사업자등록증" },
  { value: "bankbook_copy", label: "통장사본" },
  { value: "receipt", label: "영수증" },
];

const PURCHASE_DOC_ACCEPT = ".pdf,.docx,.jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff,.xls,.xlsx,.hwp,.hwpx";

function Preview({ file }: { file: ManagerFile }) {
  if (!file || file.isDirectory) return null;
  const url = previewUrl(file.path);
  const lower = file.name.toLowerCase();
  if (/\.(png|jpe?g|gif|bmp|webp|tiff?)$/.test(lower)) {
    return <img className="preview-image" src={url} alt={file.name} />;
  }
  if (lower.endsWith(".pdf")) {
    return <iframe className="preview-frame" src={url} title={file.name} />;
  }
  if (/\.(txt|csv|json|md)$/.test(lower)) {
    return <iframe className="preview-frame" src={url} title={file.name} />;
  }
  return (
    <div className="preview-empty">
      <div className="preview-title">{file.name}</div>
      <a href={downloadUrl(file.path)}>Download</a>
    </div>
  );
}

function fmtDate(value?: string) {
  if (!value) return "";
  return new Date(value).toLocaleString();
}

function jobFailed(job: { state?: string; returncode?: number | null }) {
  return job.state === "failed" || (typeof job.returncode === "number" && job.returncode !== 0);
}

function jobStateText(job: { state?: string; returncode?: number | null }) {
  const state = job.state || "unknown";
  return job.returncode == null ? state : `${state} · ${job.returncode}`;
}

function Dashboard({
  data,
  isAdmin,
  onOpenFiles,
  onOpenDocs,
  onOpenImageHelper,
  onRefresh,
}: {
  data: DashboardData | null;
  isAdmin: boolean;
  onOpenFiles: (path?: string) => void;
  onOpenDocs: (path: string) => void;
  onOpenImageHelper: (path: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const [showAllPurchases, setShowAllPurchases] = useState(false);
  const [showAllMeeting, setShowAllMeeting] = useState(false);
  if (!data) {
    return <div className="panel empty-panel">Loading dashboard...</div>;
  }
  const incompleteCases = data.purchaseCases.filter((item) => item.status === "incomplete");
  const readyCases = data.purchaseCases.filter((item) => item.status === "ready");
  const finishedCases = data.purchaseCases.filter((item) => item.status === "finished");
  const openCases = data.purchaseCases.filter((item) => !item.uploaded);
  const visiblePurchaseCases = showAllPurchases ? data.purchaseCases : openCases;
  const meetingPending = data.meeting.pendingReceiptCount ?? data.meeting.receiptCount ?? 0;
  const meetingProcessed = data.meeting.readyToEmailCount ?? data.meeting.outputCount ?? 0;
  const meetingEmailed = data.meeting.emailedCount ?? 0;
  const meetingItems = data.meeting.items || [];
  const visibleMeetingItems = showAllMeeting ? meetingItems : meetingItems.filter((item) => item.status !== "email-sent");
  return (
    <section className="dashboard">
      <div className="metric-grid">
        {isAdmin ? <div className="metric"><span>Projects</span><strong>{data.projects.length}</strong></div> : null}
        <div className="metric"><span>Purchase cases</span><strong>{data.purchaseCases.length}</strong></div>
        <div className="metric warn"><span>Incomplete</span><strong>{incompleteCases.length}</strong></div>
        <div className="metric"><span>Ready</span><strong>{readyCases.length}</strong></div>
        <div className="metric"><span>Finished</span><strong>{finishedCases.length}</strong></div>
        {isAdmin ? <div className="metric warn"><span>Unprocessed</span><strong>{meetingPending}</strong></div> : null}
        {isAdmin ? <div className="metric"><span>Processed</span><strong>{meetingProcessed}</strong></div> : null}
        {isAdmin ? <div className="metric"><span>Email sent</span><strong>{meetingEmailed}</strong></div> : null}
      </div>

      <div className={isAdmin ? "dashboard-grid" : "dashboard-grid single-panel"}>
        <div className="panel">
          <div className="panel-header">
            <h2>Purchase Status</h2>
            <div className="panel-actions">
              <button onClick={() => setShowAllPurchases((value) => !value)}>
                {showAllPurchases ? "진행중만" : "전체 보기"}
              </button>
              {isAdmin ? <button onClick={() => onOpenFiles('/purchase')}>purchase 폴더</button> : null}
            </div>
          </div>
          {!showAllPurchases && openCases.length === 0 ? <p className="muted">No open purchase cases.</p> : null}
          <div className="case-list">
            {visiblePurchaseCases.map((item) => {
              const imageActionLabel = item.uploaded ? null : item.workflowStatus === "no images" ? "Upload Images" : "Edit Images";
              const canSelectProject = !item.uploaded;
              return (
                <div
                  className={`case-row${isAdmin ? "" : " read-only"}`}
                  key={item.path}
                  onClick={() => {
                    if (isAdmin) onOpenFiles(item.path);
                  }}
                  role={isAdmin ? "button" : undefined}
                  tabIndex={isAdmin ? 0 : undefined}
                >
                  <div>
                    <strong>{item.name}</strong>
                    <span>{item.fileCount} files · {fmtDate(item.updatedAt)}</span>
                    {item.missing.length ? <span>missing: {item.missing.join(", ")}</span> : null}
                    {isAdmin ? (item.effectiveProjectId ? <span>project: {item.effectiveProjectId}</span> : <span>project: not set</span>) : null}
                  </div>
                  <div className="case-row-actions">
                    {isAdmin && canSelectProject ? (
                      <select
                        className="row-select"
                        value={item.projectId || ""}
                        onClick={(event) => event.stopPropagation()}
                        onChange={(event) => {
                          event.stopPropagation();
                          void updatePurchaseProject(item.path, event.target.value).then(() => onRefresh());
                        }}
                      >
                        <option value="">Default</option>
                        {data.projects.map((project) => (
                          <option value={project.key} key={project.key}>{project.no} - {project.name}</option>
                        ))}
                      </select>
                    ) : null}
                    <em className={`badge ${item.status} ${item.workflowStatus.replace(/\s+/g, "-")}`}>
                      {item.statusLabel}
                    </em>
                    {!item.uploaded ? (
                      <button
                        className="row-action"
                        onClick={(event) => {
                          event.stopPropagation();
                          onOpenDocs(item.path);
                        }}
                      >
                        Add Docs
                      </button>
                    ) : null}
                    {imageActionLabel ? (
                      <button
                        className="row-action"
                        onClick={(event) => {
                          event.stopPropagation();
                          onOpenImageHelper(item.path);
                        }}
                      >
                        {imageActionLabel}
                      </button>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {isAdmin ? <div className="panel">
          <div className="panel-header">
            <h2>Meeting</h2>
            <div className="panel-actions">
              <button onClick={() => setShowAllMeeting((value) => !value)}>
                {showAllMeeting ? "Open only" : "Show All"}
              </button>
              <button onClick={() => onOpenFiles('/meeting')}>Open meeting</button>
            </div>
          </div>
          <div className="case-list meeting-list">
            {visibleMeetingItems.length === 0 ? <p className="muted">No open meeting items.</p> : null}
            {visibleMeetingItems.map((item) => {
              const detail = item.detail && item.detail.includes("T") ? fmtDate(item.detail) : item.detail;
              return (
                <div className="case-row" key={`${item.status}-${item.path}`} onClick={() => onOpenFiles(item.path)} role="button" tabIndex={0}>
                  <div>
                    <strong>{item.name}</strong>
                    <span>{item.kind}{detail ? ` · ${detail}` : ""}</span>
                  </div>
                  <em className={`badge ${item.status}`}>{item.statusLabel}</em>
                </div>
              );
            })}
          </div>
        </div> : null}

        {isAdmin ? <div className="panel">
          <div className="panel-header"><h2>Recent Jobs</h2></div>
          {data.jobs.length === 0 ? <p className="muted">No jobs yet.</p> : null}
          {data.jobs.map((job) => (
            <div className={`job-row ${jobFailed(job) ? "failed" : ""}`} key={job.id}>
              <strong>{job.kind || 'job'}</strong>
              <span>{jobStateText(job)}</span>
              {job.errorSummary ? <span className="job-error">{job.errorSummary}</span> : null}
              <small>{job.id}</small>
            </div>
          ))}
        </div> : null}
      </div>
    </section>
  );
}

function PurchaseDocsModal({
  casePath,
  onClose,
  onRefreshDashboard,
}: {
  casePath: string;
  onClose: () => void;
  onRefreshDashboard: () => Promise<void>;
}) {
  const [data, setData] = useState<PurchaseDocsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [message, setMessage] = useState("");
  const [messageKind, setMessageKind] = useState<"error" | "success">("error");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setData(await loadPurchaseDocs(casePath));
      setMessage("");
    } catch (error) {
      setMessageKind("error");
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [casePath]);

  useEffect(() => { void refresh(); }, [refresh]);

  const addFiles = async (fileList: FileList | File[]) => {
    const files = Array.from(fileList);
    if (!files.length) return;
    setSaving(true);
    try {
      const result = await uploadPurchaseDocs(casePath, files);
      setData((current) => {
        const existing = new Map((current?.documents || []).map((doc) => [doc.path, doc]));
        for (const doc of result.documents) existing.set(doc.path, doc);
        return { ...(current || result), documents: Array.from(existing.values()) };
      });
      await onRefreshDashboard();
      setMessageKind("success");
      setMessage(`Uploaded ${result.documents.length} document${result.documents.length === 1 ? "" : "s"}.`);
    } catch (error) {
      setMessageKind("error");
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  const changeDocType = async (path: string, docType: PurchaseDocType) => {
    setSaving(true);
    try {
      const result = await updatePurchaseDocType(casePath, path, docType);
      setData((current) => {
        if (!current) return current;
        return {
          ...current,
          documents: current.documents.map((doc) => (doc.path === path ? result.document : doc)),
        };
      });
      await onRefreshDashboard();
      setMessageKind("success");
      setMessage("Document type updated.");
    } catch (error) {
      setMessageKind("error");
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-panel docs-modal">
        <div className="panel-header">
          <div>
            <h2>Add Docs</h2>
            <p className="muted">{data?.caseName || casePath}</p>
          </div>
          <div className="panel-actions">
            <button onClick={() => void refresh()} disabled={loading || saving}>Refresh</button>
            <button onClick={onClose}>Close</button>
          </div>
        </div>
        {message ? <div className={messageKind === "success" ? "success-banner" : "error-banner"}>{message}</div> : null}
        <div
          className={`drop-zone docs-drop-zone ${dragActive ? "active" : ""}`}
          role="button"
          tabIndex={0}
          onClick={() => fileInputRef.current?.click()}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              fileInputRef.current?.click();
            }
          }}
          onDragEnter={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setDragActive(true);
          }}
          onDragOver={(event) => {
            event.preventDefault();
            event.stopPropagation();
            event.dataTransfer.dropEffect = "copy";
          }}
          onDragLeave={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setDragActive(false);
          }}
          onDrop={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setDragActive(false);
            void addFiles(event.dataTransfer.files);
          }}
        >
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={PURCHASE_DOC_ACCEPT}
            disabled={saving}
            onChange={(event) => {
              if (event.target.files) void addFiles(event.target.files);
              event.currentTarget.value = "";
            }}
          />
          <strong>{saving ? "Saving..." : "Drop documents here"}</strong>
          <span>Files are auto-classified and can be corrected below.</span>
        </div>
        <div className="docs-table-wrap">
          <table className="docs-table">
            <thead>
              <tr>
                <th>File</th>
                <th>Type</th>
                <th>Source</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {data?.documents.length ? data.documents.map((doc) => (
                <tr key={doc.path}>
                  <td>
                    <strong>{doc.name}</strong>
                    {doc.reason ? <span>{doc.reason}</span> : null}
                  </td>
                  <td>
                    <select
                      value={doc.docType || "unknown"}
                      disabled={saving}
                      onChange={(event) => void changeDocType(doc.path, event.target.value as PurchaseDocType)}
                    >
                      {PURCHASE_DOC_OPTIONS.map((option) => (
                        <option value={option.value} key={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </td>
                  <td>{doc.classificationSource || doc.classification || "-"}</td>
                  <td>{typeof doc.confidence === "number" ? doc.confidence.toFixed(2) : "-"}</td>
                </tr>
              )) : (
                <tr>
                  <td colSpan={4}>{loading ? "Loading documents..." : "No documents yet."}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function FileBrowser({ initialPath }: { initialPath: string }) {
  const [files, setFiles] = useState<ManagerFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [dropActive, setDropActive] = useState(false);
  const [currentPath, setCurrentPath] = useState(initialPath || "/purchase");
  const dragDepth = useRef(0);

  useEffect(() => setCurrentPath(initialPath || "/purchase"), [initialPath]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const nextFiles = await listFiles();
      setFiles(nextFiles);
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const acceptedFileTypes = useMemo(() => ".pdf,.docx,.jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff,.xls,.xlsx,.hwp,.hwpx,.csv,.json,.txt", []);
  const hasExternalFiles = useCallback((event: React.DragEvent<HTMLElement>) => {
    return Array.from(event.dataTransfer.types || []).includes("Files");
  }, []);
  const droppedFiles = useCallback((event: React.DragEvent<HTMLElement>) => {
    return Array.from(event.dataTransfer.files || []);
  }, []);
  const unsupportedDropFiles = useCallback((items: File[]) => {
    return items.filter((file) => {
      const dot = file.name.lastIndexOf(".");
      const suffix = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
      return !UPLOAD_EXTENSIONS.has(suffix);
    });
  }, []);
  const run = useCallback(async (operation: () => Promise<void>) => {
    setLoading(true);
    try {
      await operation();
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [refresh]);
  const handleDragEnter = useCallback((event: React.DragEvent<HTMLElement>) => {
    if (!hasExternalFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth.current += 1;
    setDropActive(true);
  }, [hasExternalFiles]);
  const handleDragOver = useCallback((event: React.DragEvent<HTMLElement>) => {
    if (!hasExternalFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
    setDropActive(true);
  }, [hasExternalFiles]);
  const handleDragLeave = useCallback((event: React.DragEvent<HTMLElement>) => {
    if (!hasExternalFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDropActive(false);
  }, [hasExternalFiles]);
  const handleDrop = useCallback((event: React.DragEvent<HTMLElement>) => {
    if (!hasExternalFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepth.current = 0;
    setDropActive(false);
    const items = droppedFiles(event);
    if (!items.length) return;
    const unsupported = unsupportedDropFiles(items);
    if (unsupported.length) {
      setMessage(`Unsupported file type: ${unsupported.map((file) => file.name).join(", ")}`);
      return;
    }
    void run(() => uploadFiles(currentPath, items).then(() => undefined));
  }, [currentPath, droppedFiles, hasExternalFiles, run, unsupportedDropFiles]);

  return (
    <section
      className={`file-browser${dropActive ? " drop-active" : ""}`}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {message && <div className="error-banner">{message}</div>}
      {dropActive ? (
        <div className="file-drop-overlay">
          <div>
            <strong>현재 폴더에 업로드</strong>
            <span>{currentPath}</span>
          </div>
        </div>
      ) : null}
      <FileManager
        files={files}
        initialPath={currentPath}
        layout="list"
        height="calc(100vh - 150px)"
        width="100%"
        language="ko-KR"
        primaryColor="#2563eb"
        acceptedFileTypes={acceptedFileTypes}
        maxFileSize={200 * 1024 * 1024}
        isLoading={loading}
        collapsibleNav
        defaultNavExpanded
        enableFilePreview
        filePreviewComponent={(file: ManagerFile) => <Preview file={file} />}
        fileUploadConfig={{ url: "api/upload", method: "POST" }}
        onFileUploading={(_file: ManagerFile, parentFolder: ManagerFile) => ({ parentPath: parentFolder?.path || currentPath })}
        onFileUploaded={() => void refresh()}
        onFolderChange={(path: string) => setCurrentPath(path || "/purchase")}
        onRefresh={() => void refresh()}
        onCreateFolder={(name: string, parentFolder: ManagerFile) => void run(() => createFolder(parentFolder?.path || currentPath, name))}
        onRename={(file: ManagerFile, newName: string) => void run(() => renamePath(file.path, newName))}
        onDelete={(selected: ManagerFile[]) => void run(() => deletePaths(selected.map((item) => item.path)))}
        onPaste={(selected: ManagerFile[], destinationFolder: ManagerFile, operationType: "copy" | "move") =>
          void run(() => movePaths(selected.map((item) => item.path), destinationFolder.path, operationType))
        }
        onDownload={(selected: ManagerFile[]) => {
          for (const file of selected.filter((item) => !item.isDirectory)) {
            window.open(downloadUrl(file.path), "_blank", "noopener,noreferrer");
          }
        }}
        onError={(error: { message?: string }) => setMessage(error.message || "File manager error")}
        permissions={{ create: true, upload: true, move: true, copy: true, rename: true, download: true, delete: true }}
      />
    </section>
  );
}

function JobsView() {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [selectedJob, setSelectedJob] = useState<JobSummary | null>(null);
  const [stdout, setStdout] = useState("");
  const [stderr, setStderr] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setJobs(await loadJobs());
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const selectJob = useCallback(async (job: JobSummary) => {
    setSelectedJob(job);
    setStdout("");
    setStderr("");
    try {
      const [nextStdout, nextStderr] = await Promise.all([loadJobLog(job.id, "stdout"), loadJobLog(job.id, "stderr")]);
      setStdout(nextStdout);
      setStderr(nextStderr);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }, []);

  return (
    <section className="jobs-view">
      {message && <div className="error-banner">{message}</div>}
      <div className="panel">
        <div className="panel-header">
          <h2>Jobs</h2>
          <button onClick={() => void refresh()} disabled={loading}>Refresh</button>
        </div>
        {jobs.length === 0 ? <p className="muted">No jobs yet.</p> : null}
        <div className="jobs-layout">
          <div className="jobs-list">
            {jobs.map((job) => (
              <button className={`job-card ${jobFailed(job) ? "failed" : ""}`} key={job.id} onClick={() => void selectJob(job)}>
                <strong>{job.kind || "job"}</strong>
                <span>{jobStateText(job)}</span>
                {job.errorSummary ? <span className="job-error">{job.errorSummary}</span> : null}
                {job.caseDir ? <span>{job.caseDir}</span> : null}
                <small>{job.id}</small>
              </button>
            ))}
          </div>
          <div className="job-log-panel">
            {selectedJob ? (
              <>
                <h3>{selectedJob.kind || "job"}</h3>
                <p className="muted">{selectedJob.id}</p>
                <h4>stdout</h4>
                <pre>{stdout || "(empty)"}</pre>
                <h4>stderr</h4>
                <pre>{stderr || "(empty)"}</pre>
              </>
            ) : (
              <p className="muted">Select a job to view logs.</p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

const AUTOMATION_ACTIONS: Array<{ key: AutomationActionName; label: string }> = [
  { key: "collect_docs", label: "Collect Docs" },
  { key: "generate_purchase_docs", label: "Generate Purchase Docs" },
  { key: "upload_purchases", label: "Upload Purchases" },
  { key: "process_receipts", label: "Process Receipts" },
  { key: "send_meeting_mail", label: "Send mail" },
];

const HOUR_OPTIONS = Array.from({ length: 24 }, (_value, index) => index);
const DAY_OPTIONS = Array.from({ length: 28 }, (_value, index) => index + 1);

function SettingsView() {
  const [settings, setSettings] = useState<AutomationSettings | null>(null);
  const [projects, setProjects] = useState<DashboardData["projects"]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [messageKind, setMessageKind] = useState<"error" | "success">("error");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [nextSettings, allProjects] = await Promise.all([loadAutomationSettings(), loadProjects()]);
      setSettings(nextSettings);
      setProjects(allProjects);
      setMessage("");
    } catch (error) {
      setMessageKind("error");
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const updateAction = (action: AutomationActionName, patch: Partial<AutomationSettings["actions"][AutomationActionName]>) => {
    setSettings((current) => {
      if (!current) return current;
      return {
        ...current,
        actions: {
          ...current.actions,
          [action]: {
            ...current.actions[action],
            ...patch,
          },
        },
      };
    });
  };

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    try {
      setSettings(await saveAutomationSettings(settings));
      setMessageKind("success");
      setMessage("Automation settings saved.");
    } catch (error) {
      setMessageKind("error");
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  const visibleProjectIds = new Set(settings?.visibleProjectIds || []);
  const displayedProjects = projects.filter((project) => !visibleProjectIds.size || visibleProjectIds.has(project.key));
  const projectOptionLabel = (project: DashboardData["projects"][number]) => {
    const dates = project.start_date || project.end_date ? ` (${project.start_date || "?"} - ${project.end_date || "?"})` : "";
    return `${project.no} - ${project.name}${dates}`;
  };

  const toggleVisibleProject = (projectKey: string, checked: boolean) => {
    setSettings((current) => {
      if (!current) return current;
      const existing = new Set(
        current.visibleProjectIds?.length ? current.visibleProjectIds : projects.map((project) => project.key),
      );
      if (checked) existing.add(projectKey);
      else existing.delete(projectKey);
      const nextVisible = Array.from(existing);
      const defaultProjectId = current.defaultProjectId && nextVisible.length && !existing.has(current.defaultProjectId) ? "" : current.defaultProjectId;
      return { ...current, visibleProjectIds: nextVisible, defaultProjectId };
    });
  };

  return (
    <section className="settings-view">
      {message && <div className={messageKind === "success" ? "success-banner" : "error-banner"}>{message}</div>}
      <div className="panel">
        <div className="panel-header">
          <div>
            <h2>Automation Settings</h2>
            <p className="muted">Daily schedules run on the selected UTC hour. Monthly schedules run at 00:00 UTC on the selected day.</p>
          </div>
          <div className="panel-actions">
            <button onClick={() => void refresh()} disabled={loading}>Refresh</button>
            <button onClick={() => void save()} disabled={!settings || saving}>Save</button>
          </div>
        </div>
        {!settings ? <p className="muted">Loading settings...</p> : null}
        {settings ? (
          <>
          <div className="settings-default">
            <label>
              <span>Default project</span>
              <select
                value={settings.defaultProjectId || ""}
                onChange={(event) => setSettings((current) => current ? { ...current, defaultProjectId: event.target.value } : current)}
              >
                <option value="">Not set</option>
                {displayedProjects.map((project) => (
                  <option value={project.key} key={project.key}>{projectOptionLabel(project)}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Meeting email recipient</span>
              <input
                type="email"
                value={settings.meetingEmailRecipient || ""}
                onChange={(event) => setSettings((current) => current ? { ...current, meetingEmailRecipient: event.target.value } : current)}
              />
            </label>
            <label>
              <span>Notification email</span>
              <input
                type="email"
                value={settings.notificationEmailRecipient || ""}
                onChange={(event) => setSettings((current) => current ? { ...current, notificationEmailRecipient: event.target.value } : current)}
              />
            </label>
          </div>
          <div className="settings-projects">
            <div className="settings-projects-header">
              <strong>Visible projects</strong>
              <span className="muted">Unchecked projects are hidden from purchase project selectors. If none are checked, all projects are shown.</span>
            </div>
            <div className="project-check-list">
              {projects.map((project) => {
                const checked = !visibleProjectIds.size || visibleProjectIds.has(project.key);
                return (
                  <label className="project-check-row" key={project.key}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(event) => toggleVisibleProject(project.key, event.target.checked)}
                    />
                    <span>
                      <strong>{project.no}</strong>
                      <em>{project.name}</em>
                      {(project.start_date || project.end_date) ? <small>{project.start_date || "?"} - {project.end_date || "?"}</small> : <small>No date range</small>}
                    </span>
                  </label>
                );
              })}
            </div>
          </div>
          <div className="settings-table">
            <div className="settings-row settings-head">
              <span>Action</span>
              <span>Daily</span>
              <span>Hour</span>
              <span>Monthly</span>
              <span>Day</span>
            </div>
            {AUTOMATION_ACTIONS.map((action) => {
              const config = settings.actions[action.key];
              return (
                <div className="settings-row" key={action.key}>
                  <strong>{action.label}</strong>
                  <label className="check-cell">
                    <input
                      type="checkbox"
                      checked={config.dailyEnabled}
                      onChange={(event) => updateAction(action.key, { dailyEnabled: event.target.checked })}
                    />
                  </label>
                  <select
                    value={config.dailyHour}
                    disabled={!config.dailyEnabled}
                    onChange={(event) => updateAction(action.key, { dailyHour: Number(event.target.value) })}
                  >
                    {HOUR_OPTIONS.map((hour) => <option value={hour} key={hour}>{hour.toString().padStart(2, "0")}:00</option>)}
                  </select>
                  <label className="check-cell">
                    <input
                      type="checkbox"
                      checked={config.monthlyEnabled}
                      onChange={(event) => updateAction(action.key, { monthlyEnabled: event.target.checked })}
                    />
                  </label>
                  <select
                    value={config.monthlyDay}
                    disabled={!config.monthlyEnabled}
                    onChange={(event) => updateAction(action.key, { monthlyDay: Number(event.target.value) })}
                  >
                    {DAY_OPTIONS.map((day) => <option value={day} key={day}>Day {day}</option>)}
                  </select>
                </div>
              );
            })}
          </div>
          </>
        ) : null}
      </div>
    </section>
  );
}

type PendingImage = {
  file: File;
  itemNumber: number;
  previewUrl: string;
};

function hasDuplicateNumbers(values: number[]) {
  return new Set(values).size !== values.length;
}

function PurchaseImageHelper({ casePath, onBack }: { casePath: string; onBack: () => void }) {
  const [data, setData] = useState<PurchaseImageHelperData | null>(null);
  const [pending, setPending] = useState<PendingImage[]>([]);
  const [existingAssignments, setExistingAssignments] = useState<Record<string, number>>({});
  const [dragActive, setDragActive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [messageKind, setMessageKind] = useState<"error" | "success">("error");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const nextData = await loadPurchaseImageHelper(casePath);
      setData(nextData);
      setExistingAssignments(
        Object.fromEntries(nextData.images.map((image) => [image.path, image.itemNumber || 1])),
      );
      setMessage("");
    } catch (error) {
      setMessageKind("error");
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [casePath]);

  useEffect(() => { void refresh(); }, [refresh]);

  const itemCount = data?.itemCount || 0;
  const itemOptions = useMemo(() => Array.from({ length: itemCount }, (_value, index) => index + 1), [itemCount]);
  const title = data?.images.length ? "Edit Images" : "Upload Images";
  const existingNumbers = data?.images.map((image) => existingAssignments[image.path] || image.itemNumber || 1) || [];
  const pendingNumbers = pending.map((item) => item.itemNumber);
  const hasExistingDuplicates = hasDuplicateNumbers(existingNumbers);
  const hasPendingDuplicates = hasDuplicateNumbers(pendingNumbers);
  const assignmentsDirty = Boolean(
    data?.images.some((image) => (existingAssignments[image.path] || image.itemNumber || 1) !== (image.itemNumber || 1)),
  );

  const addFiles = useCallback((fileList: FileList | File[]) => {
    if (!itemCount) {
      setMessageKind("error");
      setMessage("Wait until item numbers are loaded from the quote.");
      return;
    }
    const files = Array.from(fileList).filter((file) => file.type.startsWith("image/") || /\.(jpe?g|png|bmp|tiff?)$/i.test(file.name));
    if (!files.length) return;
    setPending((current) => [
      ...current,
      ...files.map((file, index) => ({
        file,
        itemNumber: Math.min(((current.length + index) % itemCount) + 1, itemCount),
        previewUrl: URL.createObjectURL(file),
      })),
    ]);
  }, [itemCount]);

  const updatePendingItemNumber = (index: number, itemNumber: number) => {
    setPending((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, itemNumber } : item));
  };

  const updateExistingItemNumber = (path: string, itemNumber: number) => {
    setExistingAssignments((current) => ({ ...current, [path]: itemNumber }));
  };

  const removePending = (index: number) => {
    setPending((current) => {
      const item = current[index];
      if (item) URL.revokeObjectURL(item.previewUrl);
      return current.filter((_item, itemIndex) => itemIndex !== index);
    });
  };

  const deleteExisting = async (path: string) => {
    setSaving(true);
    try {
      await deletePurchaseImage(casePath, path);
      await refresh();
      setMessageKind("success");
      setMessage("Image moved to trash.");
    } catch (error) {
      setMessageKind("error");
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  const saveOrder = async () => {
    if (!data || hasExistingDuplicates) return;
    setSaving(true);
    try {
      await reorderPurchaseImages(
        casePath,
        data.images.map((image) => ({ path: image.path, itemNumber: existingAssignments[image.path] || image.itemNumber || 1 })),
      );
      await refresh();
      setMessageKind("success");
      setMessage("Image order saved.");
    } catch (error) {
      setMessageKind("error");
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  const saveImages = async () => {
    if (!pending.length || hasPendingDuplicates) return;
    setSaving(true);
    try {
      const result = await uploadPurchaseImages(casePath, pending.map((item) => item.file), pending.map((item) => item.itemNumber));
      for (const item of pending) URL.revokeObjectURL(item.previewUrl);
      setPending([]);
      await refresh();
      setMessageKind("success");
      setMessage(result.archived.length ? "Images saved. Replaced images were moved to trash." : "Images saved.");
    } catch (error) {
      setMessageKind("error");
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="image-helper">
      {message && <div className={messageKind === "success" ? "success-banner" : "error-banner"}>{message}</div>}
      <div className="panel image-helper-panel">
        <div className="panel-header">
          <div>
            <h2>{title}</h2>
            <p className="muted">{data?.caseName || casePath} · items 1-{itemCount}</p>
          </div>
          <div className="panel-actions">
            <button onClick={onBack}>Back</button>
            <button onClick={() => void refresh()} disabled={loading}>Refresh</button>
          </div>
        </div>
        <div className="image-helper-grid">
          <div className="quote-preview-panel">
            {data?.quotePath ? (
              <iframe className="quote-preview" src={previewUrl(data.quotePath)} title="Quote preview" />
            ) : (
              <div className="preview-empty">No quote file found.</div>
            )}
          </div>
          <div className="image-upload-panel">
            <h3>Current Images</h3>
            <div className="saved-images">
              {data?.images.length ? data.images.map((image) => (
                <div className="saved-image-row editable" key={image.path}>
                  <img src={previewUrl(image.path)} alt={image.name} />
                  <div>
                    <strong>{image.name}</strong>
                    <select
                      value={existingAssignments[image.path] || image.itemNumber || 1}
                      onChange={(event) => updateExistingItemNumber(image.path, Number(event.target.value))}
                    >
                      {itemOptions.map((option) => <option value={option} key={option}>Item {option}</option>)}
                    </select>
                  </div>
                  <button disabled={saving} onClick={() => void deleteExisting(image.path)}>Delete</button>
                </div>
              )) : <p className="muted">No saved images.</p>}
            </div>
            {hasExistingDuplicates ? <p className="form-warning">Each current image must use a different item number.</p> : null}
            <button className="save-images-button secondary" onClick={() => void saveOrder()} disabled={!assignmentsDirty || hasExistingDuplicates || saving}>
              Save Order
            </button>

            <h3>Upload or Replace</h3>
            <div
              className={`drop-zone ${dragActive ? "active" : ""} ${!itemCount ? "disabled" : ""}`}
              role="button"
              tabIndex={0}
              onClick={() => {
                if (itemCount) fileInputRef.current?.click();
              }}
              onKeyDown={(event) => {
                if ((event.key === "Enter" || event.key === " ") && itemCount) {
                  event.preventDefault();
                  fileInputRef.current?.click();
                }
              }}
              onDragEnter={(event) => {
                event.preventDefault();
                event.stopPropagation();
                if (itemCount) setDragActive(true);
              }}
              onDragOver={(event) => {
                event.preventDefault();
                event.stopPropagation();
                if (itemCount) event.dataTransfer.dropEffect = "copy";
              }}
              onDragLeave={(event) => {
                event.preventDefault();
                event.stopPropagation();
                setDragActive(false);
              }}
              onDrop={(event) => {
                event.preventDefault();
                event.stopPropagation();
                setDragActive(false);
                addFiles(event.dataTransfer.files);
              }}
            >
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept="image/*,.jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff"
                disabled={!itemCount}
                onChange={(event) => {
                  if (event.target.files) addFiles(event.target.files);
                  event.currentTarget.value = "";
                }}
              />
              <strong>{itemCount ? "Drop images here" : loading ? "Loading item numbers..." : "Item numbers unavailable"}</strong>
              <span>{itemCount ? "Choosing an occupied item number replaces that image." : "Quote parsing must finish before image upload."}</span>
            </div>

            <div className="pending-images">
              {pending.map((item, index) => (
                <div className="pending-image-row" key={`${item.file.name}-${index}`}>
                  <img src={item.previewUrl} alt={item.file.name} />
                  <div>
                    <strong>{item.file.name}</strong>
                    <select value={item.itemNumber} onChange={(event) => updatePendingItemNumber(index, Number(event.target.value))}>
                      {itemOptions.map((option) => <option value={option} key={option}>Item {option}</option>)}
                    </select>
                  </div>
                  <button onClick={() => removePending(index)}>Remove</button>
                </div>
              ))}
            </div>
            {hasPendingDuplicates ? <p className="form-warning">Upload one new image per item number.</p> : null}

            <button className="save-images-button" onClick={() => void saveImages()} disabled={!pending.length || hasPendingDuplicates || saving}>
              Save Images
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

function App() {
  const [view, setView] = useState<View>("dashboard");
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [browserPath, setBrowserPath] = useState("/purchase");
  const [imageHelperPath, setImageHelperPath] = useState("/purchase");
  const [docsCasePath, setDocsCasePath] = useState<string | null>(null);
  const [actionRunning, setActionRunning] = useState<ActionName | null>(null);
  const [message, setMessage] = useState("");
  const [messageKind, setMessageKind] = useState<"error" | "success">("error");
  const activeJobKinds = useMemo(
    () =>
      new Set(
        (dashboard?.jobs || [])
          .filter((job) => job.state === "queued" || job.state === "running")
          .map((job) => job.kind)
          .filter(Boolean),
      ),
    [dashboard?.jobs],
  );
  const activeJobCount = activeJobKinds.size;
  const isAdmin = session?.admin === true;

  const refreshDashboard = useCallback(async () => {
    try {
      const nextDashboard = await loadDashboard();
      setDashboard(nextDashboard);
      const latestJob = session?.admin ? nextDashboard.jobs[0] : null;
      if (latestJob && jobFailed(latestJob)) {
        setMessageKind("error");
        setMessage(
          `Job failed: ${latestJob.kind || "job"} (${latestJob.returncode ?? "unknown"}). ${
            latestJob.errorSummary || "Open Jobs for logs."
          }`,
        );
      }
    } catch (error) {
      setMessageKind("error");
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }, [session?.admin]);

  useEffect(() => {
    void loadSession()
      .then((nextSession) => setSession(nextSession))
      .catch((error) => {
        setMessageKind("error");
        setMessage(error instanceof Error ? error.message : String(error));
      });
  }, []);

  useEffect(() => { void refreshDashboard(); }, [refreshDashboard]);

  useEffect(() => {
    if (isAdmin || view === "dashboard" || view === "images") return;
    setView("dashboard");
  }, [isAdmin, view]);

  useEffect(() => {
    if (!activeJobCount) return;
    const timer = window.setInterval(() => {
      void refreshDashboard();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [activeJobCount, refreshDashboard]);

  const openFiles = (path = "/purchase") => {
    if (!isAdmin) return;
    setBrowserPath(path);
    setView("files");
  };

  const openImageHelper = (path: string) => {
    setImageHelperPath(path);
    setView("images");
  };

  const openDocs = (path: string) => {
    setDocsCasePath(path);
  };

  const runAction = async (action: ActionName, label: string) => {
    if (!isAdmin) return;
    setActionRunning(action);
    try {
      const result = await startAction(action);
      setMessageKind("success");
      setMessage(`${label}: started ${result.jobs.length} job${result.jobs.length === 1 ? "" : "s"}.`);
      await refreshDashboard();
    } catch (error) {
      setMessageKind("error");
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setActionRunning(null);
    }
  };

  const renderCurrentView = () => {
    if (view === "dashboard") return <Dashboard data={dashboard} isAdmin={isAdmin} onOpenFiles={openFiles} onOpenDocs={openDocs} onOpenImageHelper={openImageHelper} onRefresh={refreshDashboard} />;
    if (view === "files") return <FileBrowser initialPath={browserPath} />;
    if (view === "images") return <PurchaseImageHelper casePath={imageHelperPath} onBack={() => setView("dashboard")} />;
    if (view === "settings") return <SettingsView />;
    return <JobsView />;
  };

  const isActionBusy = (action: ActionName) => actionRunning === action || activeJobKinds.has(action);

  return (
    <main className="app">
      <header className="topbar">
        <div>
          <h1>Paperworks</h1>
          <p>Dashboard and file manager for meeting/ and purchase/ paperwork.</p>
        </div>
        <nav className="nav-tabs">
          <button className={view === "dashboard" ? "active" : ""} onClick={() => setView("dashboard")}>Dashboard</button>
          {isAdmin ? <button className={view === "files" ? "active" : ""} onClick={() => setView("files")}>File Browser</button> : null}
          <button onClick={() => void refreshDashboard()}>Refresh</button>
          {isAdmin ? <button className={view === "settings" ? "active" : ""} onClick={() => setView("settings")}>Settings</button> : null}
        </nav>
      </header>
      {isAdmin ? <div className="action-bar">
        <button className={isActionBusy("collect_docs") ? "running" : ""} disabled={isActionBusy("collect_docs")} onClick={() => void runAction("collect_docs", "Collect Docs")}>Collect Docs</button>
        <button className={isActionBusy("generate_purchase_docs") ? "running" : ""} disabled={isActionBusy("generate_purchase_docs")} onClick={() => void runAction("generate_purchase_docs", "Generate Purchase Docs")}>Generate Purchase Docs</button>
        <button className={isActionBusy("upload_purchases") ? "running" : ""} disabled={isActionBusy("upload_purchases")} onClick={() => void runAction("upload_purchases", "Upload Purchases")}>Upload Purchases</button>
        <button className={isActionBusy("process_receipts") ? "running" : ""} disabled={isActionBusy("process_receipts")} onClick={() => void runAction("process_receipts", "Process Receipts")}>Process Receipts</button>
        <button className={isActionBusy("send_meeting_mail") ? "running" : ""} disabled={isActionBusy("send_meeting_mail")} onClick={() => void runAction("send_meeting_mail", "Send mail")}>Send mail</button>
        <button onClick={() => setView("jobs")}>Jobs</button>
      </div> : null}
      {message && <div className={messageKind === "success" ? "success-banner" : "error-banner"}>{message}</div>}
      {renderCurrentView()}
      {docsCasePath ? <PurchaseDocsModal casePath={docsCasePath} onClose={() => setDocsCasePath(null)} onRefreshDashboard={refreshDashboard} /> : null}
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
