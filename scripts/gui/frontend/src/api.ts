export type FileItem = {
  name: string;
  isDirectory: boolean;
  path: string;
  updatedAt?: string;
  size?: number;
  mimeType?: string;
};

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      detail = data.detail || detail;
    } catch {
      // Keep HTTP status text.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export async function listFiles(): Promise<FileItem[]> {
  const data = await requestJson<{ files: FileItem[] }>("api/files");
  return data.files;
}

export async function uploadFiles(parentPath: string, files: File[]): Promise<FileItem[]> {
  const form = new FormData();
  form.append("parentPath", parentPath);
  for (const file of files) {
    form.append("file", file);
  }
  const data = await requestJson<{ uploaded: FileItem[] }>("api/upload", {
    method: "POST",
    body: form,
  });
  return data.uploaded;
}

export async function createFolder(parentPath: string, name: string): Promise<void> {
  await requestJson("api/folders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parentPath, name }),
  });
}

export async function renamePath(path: string, newName: string): Promise<void> {
  await requestJson("api/rename", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, newName }),
  });
}

export async function deletePaths(paths: string[]): Promise<void> {
  await requestJson("api/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
}

export async function movePaths(paths: string[], destinationPath: string, operation: "move" | "copy"): Promise<void> {
  await requestJson("api/move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths, destinationPath, operation }),
  });
}

export function previewUrl(path: string): string {
  return `api/preview?path=${encodeURIComponent(path)}`;
}

export function downloadUrl(path: string): string {
  return `api/download?path=${encodeURIComponent(path)}`;
}

export type ProjectInfo = { key: string; no: string; name: string; start_date?: string; end_date?: string };

export type SessionInfo = {
  user: string;
  role: "admin" | "user";
  admin: boolean;
};

export async function loadSession(): Promise<SessionInfo> {
  return requestJson<SessionInfo>("api/session");
}

export type DashboardData = {
  projects: ProjectInfo[];
  purchaseCases: Array<{
    name: string;
    path: string;
    status: "incomplete" | "ready" | "finished";
    workflowStatus: "no images" | "images found" | "generated" | "uploaded";
    itemsStatus?: "pending" | "generated" | "failed" | string;
    itemsError?: string;
    statusLabel: string;
    imageCount: number;
    generated: boolean;
    uploaded: boolean;
    missing: string[];
    required: Record<string, string[]>;
    fileCount: number;
    updatedAt: string;
    projectId?: string;
    effectiveProjectId?: string;
  }>;
  meeting: {
    pendingReceiptCount?: number;
    readyToEmailCount?: number;
    emailedCount?: number;
    outputCount?: number;
    receiptCount?: number;
    recordsCsv?: boolean;
    summaryCsv?: boolean;
    items?: Array<{
      name: string;
      path: string;
      status: "unprocessed" | "processed" | "email-sent";
      statusLabel: string;
      kind: string;
      detail?: string;
      updatedAt?: string;
    }>;
  };
  jobs: Array<{ id: string; kind?: string; state?: string; returncode?: number | null; createdAt?: string; finishedAt?: string; errorSummary?: string }>;
};

export async function loadDashboard(): Promise<DashboardData> {
  return requestJson<DashboardData>("api/dashboard");
}

export type AutomationActionName = "collect_docs" | "generate_purchase_docs" | "upload_purchases" | "process_receipts" | "send_meeting_mail";

export type ActionName = AutomationActionName;

export type AutomationActionSettings = {
  dailyEnabled: boolean;
  dailyHour: number;
  monthlyEnabled: boolean;
  monthlyDay: number;
};

export type AutomationSettings = {
  timezone: string;
  monthlyHour: number;
  defaultProjectId: string;
  visibleProjectIds: string[];
  meetingEmailRecipient: string;
  notificationEmailRecipient: string;
  actions: Record<AutomationActionName, AutomationActionSettings>;
};

export type JobSummary = {
  id: string;
  kind?: string;
  state?: string;
  returncode?: number | null;
  createdAt?: string;
  startedAt?: string;
  finishedAt?: string;
  caseDir?: string;
  count?: number;
  errorSummary?: string;
};

export type ActionResult = {
  jobs: JobSummary[];
  skipped?: Array<{ case: string; reason: string }>;
};

export async function startAction(action: ActionName): Promise<ActionResult> {
  return requestJson<ActionResult>(`api/actions/${action}`, { method: "POST" });
}

export async function loadJobs(): Promise<JobSummary[]> {
  const data = await requestJson<{ jobs: JobSummary[] }>("api/jobs");
  return data.jobs;
}

export async function loadJobLog(jobId: string, stream: "stdout" | "stderr"): Promise<string> {
  const data = await requestJson<{ text: string }>(`api/jobs/${encodeURIComponent(jobId)}/${stream}`);
  return data.text;
}

export async function loadAutomationSettings(): Promise<AutomationSettings> {
  const data = await requestJson<{ settings: AutomationSettings }>("api/automation-settings");
  return data.settings;
}

export async function saveAutomationSettings(settings: AutomationSettings): Promise<AutomationSettings> {
  const data = await requestJson<{ settings: AutomationSettings }>("api/automation-settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings }),
  });
  return data.settings;
}

export async function loadProjects(): Promise<ProjectInfo[]> {
  const data = await requestJson<{ projects: ProjectInfo[] }>("api/projects");
  return data.projects;
}

export async function updatePurchaseProject(casePath: string, projectId: string): Promise<{ casePath: string; projectId: string }> {
  return requestJson<{ casePath: string; projectId: string }>("api/purchase-project", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ casePath, projectId }),
  });
}

export type PurchaseDocType =
  | "unknown"
  | "tax_invoice"
  | "estimate"
  | "statement"
  | "business_registration"
  | "bankbook_copy"
  | "receipt";

export type PurchaseDocumentInfo = {
  name: string;
  path: string;
  docType: PurchaseDocType | string;
  docTypeLabel: string;
  allDocTypes: string[];
  classification?: string;
  classificationSource?: string;
  confidence?: number | null;
  reason?: string;
  updatedAt?: string;
};

export type PurchaseDocsData = {
  casePath: string;
  caseName: string;
  documents: PurchaseDocumentInfo[];
};

export async function loadPurchaseDocs(casePath: string): Promise<PurchaseDocsData> {
  return requestJson<PurchaseDocsData>(`api/purchase-docs?casePath=${encodeURIComponent(casePath)}`);
}

export async function uploadPurchaseDocs(casePath: string, files: File[]): Promise<PurchaseDocsData> {
  const form = new FormData();
  form.append("casePath", casePath);
  for (const file of files) {
    form.append("file", file);
  }
  return requestJson<PurchaseDocsData>("api/purchase-docs/upload", {
    method: "POST",
    body: form,
  });
}

export async function updatePurchaseDocType(casePath: string, path: string, docType: PurchaseDocType): Promise<{ document: PurchaseDocumentInfo }> {
  return requestJson<{ document: PurchaseDocumentInfo }>("api/purchase-docs/doc-type", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ casePath, path, docType }),
  });
}


export type PurchaseImageInfo = {
  name: string;
  path: string;
  itemNumber?: number | null;
  size: number;
  updatedAt: string;
};

export type PurchaseImageHelperData = {
  casePath: string;
  caseName: string;
  quotePath?: string | null;
  itemCount: number;
  images: PurchaseImageInfo[];
};

export async function loadPurchaseImageHelper(casePath: string): Promise<PurchaseImageHelperData> {
  return requestJson<PurchaseImageHelperData>(`api/purchase-image-helper?casePath=${encodeURIComponent(casePath)}`);
}

export async function uploadPurchaseImages(casePath: string, files: File[], itemNumbers: number[]): Promise<{ uploaded: PurchaseImageInfo[]; archived: string[]; itemCount: number }> {
  const form = new FormData();
  form.append("casePath", casePath);
  form.append("itemNumbers", JSON.stringify(itemNumbers));
  for (const file of files) {
    form.append("file", file);
  }
  return requestJson<{ uploaded: PurchaseImageInfo[]; archived: string[]; itemCount: number }>("api/purchase-image-helper/upload", {
    method: "POST",
    body: form,
  });
}

export async function deletePurchaseImage(casePath: string, path: string): Promise<{ deleted: string; archived: string }> {
  return requestJson<{ deleted: string; archived: string }>("api/purchase-image-helper/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ casePath, path }),
  });
}

export async function reorderPurchaseImages(
  casePath: string,
  assignments: Array<{ path: string; itemNumber: number }>,
): Promise<{ images: PurchaseImageInfo[] }> {
  return requestJson<{ images: PurchaseImageInfo[] }>("api/purchase-image-helper/reorder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ casePath, assignments }),
  });
}
