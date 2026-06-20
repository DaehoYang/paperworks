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

export type DashboardData = {
  projects: Array<{ key: string; no: string; name: string }>;
  purchaseCases: Array<{
    name: string;
    path: string;
    status: "incomplete" | "ready" | "finished";
    workflowStatus: "no images" | "images found" | "generated" | "uploaded";
    statusLabel: string;
    imageCount: number;
    generated: boolean;
    uploaded: boolean;
    missing: string[];
    required: Record<string, string[]>;
    fileCount: number;
    updatedAt: string;
  }>;
  meeting: { receiptCount: number; outputCount: number; recordsCsv: boolean; summaryCsv: boolean };
  jobs: Array<{ id: string; kind?: string; state?: string; returncode?: number | null; createdAt?: string; finishedAt?: string; errorSummary?: string }>;
};

export async function loadDashboard(): Promise<DashboardData> {
  return requestJson<DashboardData>("api/dashboard");
}

export type ActionName = "collect_docs" | "generate_purchase_docs" | "upload_purchases" | "process_receipts";

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
