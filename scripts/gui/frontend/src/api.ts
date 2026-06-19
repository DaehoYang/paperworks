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
    missing: string[];
    required: Record<string, string[]>;
    fileCount: number;
    updatedAt: string;
  }>;
  meeting: { receiptCount: number; outputCount: number; recordsCsv: boolean; summaryCsv: boolean };
  jobs: Array<{ id: string; kind?: string; state?: string; returncode?: number | null; createdAt?: string; finishedAt?: string }>;
};

export async function loadDashboard(): Promise<DashboardData> {
  return requestJson<DashboardData>("api/dashboard");
}
