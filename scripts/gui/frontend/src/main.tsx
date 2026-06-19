import React, { useCallback, useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import { FileManager } from "@cubone/react-file-manager";
import "@cubone/react-file-manager/dist/style.css";
import "./styles.css";
import {
  DashboardData,
  FileItem,
  createFolder,
  deletePaths,
  downloadUrl,
  listFiles,
  loadDashboard,
  movePaths,
  previewUrl,
  renamePath,
} from "./api";

type ManagerFile = FileItem;
type View = "dashboard" | "files";

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

function Dashboard({ data, onOpenFiles }: { data: DashboardData | null; onOpenFiles: (path?: string) => void }) {
  const [showAllPurchases, setShowAllPurchases] = useState(false);
  if (!data) {
    return <div className="panel empty-panel">Loading dashboard...</div>;
  }
  const missingCases = data.purchaseCases.filter((item) => item.missing.length > 0);
  const visiblePurchaseCases = showAllPurchases ? data.purchaseCases : missingCases;
  return (
    <section className="dashboard">
      <div className="metric-grid">
        <div className="metric"><span>Projects</span><strong>{data.projects.length}</strong></div>
        <div className="metric"><span>Purchase cases</span><strong>{data.purchaseCases.length}</strong></div>
        <div className="metric warn"><span>Missing docs</span><strong>{missingCases.length}</strong></div>
        <div className="metric"><span>Meeting receipts</span><strong>{data.meeting.receiptCount}</strong></div>
        <div className="metric"><span>Meeting outputs</span><strong>{data.meeting.outputCount}</strong></div>
      </div>

      <div className="dashboard-grid">
        <div className="panel">
          <div className="panel-header">
            <h2>{showAllPurchases ? "Purchase Status" : "Missing Documents"}</h2>
            <div className="panel-actions">
              <button onClick={() => setShowAllPurchases((value) => !value)}>
                {showAllPurchases ? "Show missing" : "Show all"}
              </button>
              <button onClick={() => onOpenFiles('/purchase')}>Open purchase</button>
            </div>
          </div>
          {!showAllPurchases && missingCases.length === 0 ? <p className="muted">No filename-based missing document flags.</p> : null}
          <div className="case-list">
            {visiblePurchaseCases.map((item) => (
              <button className="case-row" key={item.path} onClick={() => onOpenFiles(item.path)}>
                <div><strong>{item.name}</strong><span>{item.fileCount} files · {fmtDate(item.updatedAt)}</span></div>
                <em className={item.missing.length ? "badge bad" : "badge good"}>
                  {item.missing.length ? item.missing.join(", ") : "ready"}
                </em>
              </button>
            ))}
          </div>
        </div>

        <div className="panel">
          <div className="panel-header"><h2>Meeting</h2><button onClick={() => onOpenFiles('/meeting')}>Open meeting</button></div>
          <div className="kv"><span>Receipt files</span><strong>{data.meeting.receiptCount}</strong></div>
          <div className="kv"><span>Output PDFs</span><strong>{data.meeting.outputCount}</strong></div>
          <div className="kv"><span>records.csv</span><strong>{data.meeting.recordsCsv ? 'yes' : 'no'}</strong></div>
          <div className="kv"><span>summary.csv</span><strong>{data.meeting.summaryCsv ? 'yes' : 'no'}</strong></div>
        </div>

        <div className="panel">
          <div className="panel-header"><h2>Recent Jobs</h2></div>
          {data.jobs.length === 0 ? <p className="muted">No jobs yet.</p> : null}
          {data.jobs.map((job) => (
            <div className="job-row" key={job.id}>
              <strong>{job.kind || 'job'}</strong>
              <span>{job.state} · {job.returncode ?? ''}</span>
              <small>{job.id}</small>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function FileBrowser({ initialPath }: { initialPath: string }) {
  const [files, setFiles] = useState<ManagerFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [currentPath, setCurrentPath] = useState(initialPath || "/purchase");

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

  const acceptedFileTypes = useMemo(() => ".pdf,.jpg,.jpeg,.png,.bmp,.tif,.tiff,.xls,.xlsx,.hwp,.hwpx,.csv,.txt", []);
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

  return (
    <section className="file-browser">
      {message && <div className="error-banner">{message}</div>}
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

function App() {
  const [view, setView] = useState<View>("dashboard");
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [browserPath, setBrowserPath] = useState("/purchase");
  const [message, setMessage] = useState("");

  const refreshDashboard = useCallback(async () => {
    try {
      setDashboard(await loadDashboard());
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }, []);

  useEffect(() => { void refreshDashboard(); }, [refreshDashboard]);

  const openFiles = (path = "/purchase") => {
    setBrowserPath(path);
    setView("files");
  };

  return (
    <main className="app">
      <header className="topbar">
        <div>
          <h1>Paperworks</h1>
          <p>Dashboard and file manager for meeting/ and purchase/ paperwork.</p>
        </div>
        <nav className="nav-tabs">
          <button className={view === "dashboard" ? "active" : ""} onClick={() => setView("dashboard")}>Dashboard</button>
          <button className={view === "files" ? "active" : ""} onClick={() => setView("files")}>File Browser</button>
          <button onClick={() => void refreshDashboard()}>Refresh</button>
        </nav>
      </header>
      {message && <div className="error-banner">{message}</div>}
      {view === "dashboard" ? <Dashboard data={dashboard} onOpenFiles={openFiles} /> : <FileBrowser initialPath={browserPath} />}
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
