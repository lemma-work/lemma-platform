/* Where a page keeps what it owns. Plain functions, apart from the React
   context in page-context.tsx, so tests and non-React code can use them. */

/** Where a page keeps what belongs to it: `/pages/Plan.md` keeps its images
 *  in `/pages/Plan-files/` and its sub-pages in `/pages/Plan/`. */
export function pageDirs(path: string): { assets: string; children: string } {
    const cut = path.lastIndexOf("/");
    const dir = cut > 0 ? path.slice(0, cut) : "";
    const stem = path.slice(cut + 1).replace(/\.(md|markdown)$/i, "");
    return { assets: dir + "/" + stem + "-files", children: dir + "/" + stem };
}

/** A file name safe to put in a markdown link without angle brackets. */
export function safeName(name: string): string {
    return name.trim().replace(/\s+/g, "-").replace(/[^\w.-]/g, "") || "file";
}
