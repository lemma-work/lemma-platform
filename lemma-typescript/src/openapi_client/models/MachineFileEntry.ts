/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type MachineFileEntry = {
    kind: MachineFileEntry.kind;
    mode?: (number | null);
    modified_at: string;
    name: string;
    path: string;
    sha256?: (string | null);
    size_bytes: number;
};
export namespace MachineFileEntry {
    export enum kind {
        FILE = 'file',
        DIRECTORY = 'directory',
        SYMLINK = 'symlink',
    }
}
