declare module "@cubone/react-file-manager" {
  import type { CSSProperties, ReactNode } from "react";

  export type FileManagerFile = {
    name: string;
    isDirectory: boolean;
    path: string;
    updatedAt?: string;
    size?: number;
    [key: string]: unknown;
  };

  export type FileManagerProps = {
    files: FileManagerFile[];
    acceptedFileTypes?: string;
    className?: string;
    collapsibleNav?: boolean;
    defaultNavExpanded?: boolean;
    enableFilePreview?: boolean;
    filePreviewPath?: string;
    filePreviewComponent?: (file: FileManagerFile) => ReactNode;
    fileUploadConfig?: { url: string; method?: "POST" | "PUT"; headers?: Record<string, string> };
    fontFamily?: string;
    formatDate?: (date: string | Date) => string;
    height?: string | number;
    initialPath?: string;
    isLoading?: boolean;
    language?: string;
    layout?: "list" | "grid";
    maxFileSize?: number;
    onCopy?: (files: FileManagerFile[]) => void;
    onCut?: (files: FileManagerFile[]) => void;
    onCreateFolder?: (name: string, parentFolder: FileManagerFile) => void;
    onDelete?: (files: FileManagerFile[]) => void;
    onDownload?: (files: FileManagerFile[]) => void;
    onError?: (error: { type?: string; message?: string }, file?: FileManagerFile) => void;
    onFileOpen?: (file: FileManagerFile) => void;
    onFolderChange?: (path: string) => void;
    onFileUploaded?: (response: Record<string, unknown>) => void;
    onFileUploading?: (file: FileManagerFile, parentFolder: FileManagerFile) => Record<string, unknown>;
    onLayoutChange?: (layout: "list" | "grid") => void;
    onPaste?: (files: FileManagerFile[], destinationFolder: FileManagerFile, operationType: "copy" | "move") => void;
    onRefresh?: () => void;
    onRename?: (file: FileManagerFile, newName: string) => void;
    onSelectionChange?: (files: FileManagerFile[]) => void;
    permissions?: {
      create?: boolean;
      upload?: boolean;
      move?: boolean;
      copy?: boolean;
      rename?: boolean;
      download?: boolean;
      delete?: boolean;
    };
    primaryColor?: string;
    style?: CSSProperties;
    width?: string | number;
  };

  export function FileManager(props: FileManagerProps): JSX.Element;
}
