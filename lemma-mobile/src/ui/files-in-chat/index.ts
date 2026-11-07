/** Files as a chat draws them, for any chat: a chat with a Lemma and a
 *  group draw the same pieces. Nothing here knows whose chat it is in; the
 *  caller says where the bytes are and what a tap opens.
 *
 *  - `PhotoGrid` (one large, two small, "+N") and `PhotoViewer` (whole
 *    screen on black: swipe, pinch, "1 of 5", share, swipe down to close).
 *  - `DocumentCard`: first page, name, kind · pages · size; grows into the
 *    reader it opens.
 *  - `SheetPreview`: a spreadsheet's or a CSV's first rows and its count.
 *  - `AudioRow`: a recording, played in place.
 *  - `AttachTray`: camera, the latest photos, files, above the box.
 *  - `Grown` / `useGrow`: a card grown to the whole screen and back. */

export { AttachToggle, AttachTray } from "./attach-tray";
export { AudioRow } from "./audio-row";
export { DocumentCard, Reading } from "./document-card";
export { Grown, GrownPage, rectOf, useGrow, useGrowInto, type Rect } from "./grow";
export { FileGlyph } from "./glyphs";
export { barsOf, documentLine, ofCount, photoTiles, readSheet, rowCount, sheetFromMarkdown, type Sheet } from "./model";
export { PhotoGrid, type Photo } from "./photo-grid";
export { PhotoViewer } from "./photo-viewer";
export { SheetPreview } from "./sheet-preview";
