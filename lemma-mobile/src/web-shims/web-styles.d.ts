/* A web component's stylesheet, met only through a type the phone reads
   from it (`import type` is gone by the time Metro bundles): nothing here
   loads CSS, it only lets the type check walk past the import. */
declare module "*.css";
