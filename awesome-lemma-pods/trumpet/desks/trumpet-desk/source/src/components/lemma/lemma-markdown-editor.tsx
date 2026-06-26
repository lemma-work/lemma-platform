import * as React from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Eye, Edit3, SplitSquareHorizontal } from "lucide-react"
import { Button } from "@/components/lemma/ui/button"
import { Tabs, TabsList, TabsTrigger } from "@/components/lemma/ui/tabs"
import { Textarea } from "@/components/lemma/ui/textarea"
import { cn } from "@/components/lemma/lib/utils"

export type LemmaMarkdownEditorAppearance = "default" | "minimal" | "borderless" | "contained"
export type LemmaMarkdownEditorDensity = "compact" | "comfortable" | "spacious"
export type LemmaMarkdownEditorRadius = "none" | "sm" | "md" | "lg" | "xl"
export type LemmaMarkdownEditorMode = "write" | "preview" | "split"

export interface LemmaMarkdownEditorProps {
  value?: string
  defaultValue?: string
  onChange?: (value: string) => void
  placeholder?: string
  mode?: LemmaMarkdownEditorMode
  onModeChange?: (mode: LemmaMarkdownEditorMode) => void
  minRows?: number
  readOnly?: boolean
  appearance?: LemmaMarkdownEditorAppearance
  density?: LemmaMarkdownEditorDensity
  radius?: LemmaMarkdownEditorRadius
  title?: React.ReactNode
  actions?: React.ReactNode
  className?: string
}

export function LemmaMarkdownEditor({
  value,
  defaultValue = "",
  onChange,
  placeholder = "Write markdown...",
  mode,
  onModeChange,
  minRows = 12,
  readOnly,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  title,
  actions,
  className,
}: LemmaMarkdownEditorProps) {
  const [uncontrolledValue, setUncontrolledValue] = React.useState(defaultValue)
  const [uncontrolledMode, setUncontrolledMode] = React.useState<LemmaMarkdownEditorMode>("split")
  const currentValue = value ?? uncontrolledValue
  const currentMode = mode ?? uncontrolledMode

  const setValue = React.useCallback((nextValue: string) => {
    if (value == null) setUncontrolledValue(nextValue)
    onChange?.(nextValue)
  }, [onChange, value])

  const setMode = React.useCallback((nextMode: LemmaMarkdownEditorMode) => {
    if (mode == null) setUncontrolledMode(nextMode)
    onModeChange?.(nextMode)
  }, [mode, onModeChange])

  return (
    <div
      data-appearance={appearance}
      data-density={density}
      data-radius={radius}
      className={cn("lemma-markdown-editor flex min-h-0 flex-col overflow-hidden", surfaceClassName(appearance, radius), className)}
    >
      <div className={cn("flex items-center justify-between gap-3", headerClassName(appearance, density))}>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">{title ?? "Markdown"}</p>
          <p className="text-xs text-muted-foreground">{currentValue.length} characters</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Tabs value={currentMode} onValueChange={(next) => setMode(next as LemmaMarkdownEditorMode)}>
            <TabsList className={cn("h-8", radiusClassName(radius, "control"))}>
              <TabsTrigger value="write" className="gap-1.5">
                <Edit3 data-icon="inline-start" />
                Write
              </TabsTrigger>
              <TabsTrigger value="preview" className="gap-1.5">
                <Eye data-icon="inline-start" />
                Preview
              </TabsTrigger>
              <TabsTrigger value="split" className="gap-1.5">
                <SplitSquareHorizontal data-icon="inline-start" />
                Split
              </TabsTrigger>
            </TabsList>
          </Tabs>
          {actions}
        </div>
      </div>

      <div className={cn("grid min-h-0 flex-1", currentMode === "split" ? "grid-cols-1 md:grid-cols-2" : "grid-cols-1")}>
        {currentMode !== "preview" ? (
          <div className={cn("min-h-0 border-border/20", currentMode === "split" ? "md:border-r" : null)}>
            <Textarea
              value={currentValue}
              onChange={(event) => setValue(event.target.value)}
              placeholder={placeholder}
              readOnly={readOnly}
              rows={minRows}
              className={cn(
                "h-full min-h-64 resize-none rounded-none border-0 bg-transparent text-base leading-relaxed shadow-none focus-visible:ring-0",
                bodyPaddingClassName(density),
              )}
            />
          </div>
        ) : null}

        {currentMode !== "write" ? (
          <div className={cn("min-h-0 overflow-auto", bodyPaddingClassName(density))}>
            {currentValue.trim() ? (
              <div className="prose prose-neutral max-w-none dark:prose-invert">
                <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
                  {currentValue}
                </ReactMarkdown>
              </div>
            ) : (
              <div className="flex min-h-48 flex-col items-center justify-center gap-3 text-center text-muted-foreground">
                <Button variant="outline" size="sm" onClick={() => setMode("write")}>
                  <Edit3 data-icon="inline-start" />
                  Start writing
                </Button>
                <p className="text-sm">Preview appears here.</p>
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function surfaceClassName(appearance: LemmaMarkdownEditorAppearance, radius: LemmaMarkdownEditorRadius) {
  return cn(
    radiusClassName(radius, "surface"),
    appearance === "minimal" ? "border-0 bg-transparent shadow-none" : null,
    appearance === "borderless" ? "border-0 bg-transparent shadow-none" : null,
    appearance === "contained" ? "border border-border/70 bg-card shadow-sm" : null,
    appearance === "default" ? "border border-border/50 bg-card shadow-sm" : null,
  )
}

function headerClassName(appearance: LemmaMarkdownEditorAppearance, density: LemmaMarkdownEditorDensity) {
  return cn(
    appearance === "borderless" ? "border-b-0" : appearance === "minimal" ? "border-b border-border/15" : "border-b border-border/50",
    density === "compact" ? "px-3 py-2" : density === "spacious" ? "px-5 py-4" : "px-4 py-3",
  )
}

function bodyPaddingClassName(density: LemmaMarkdownEditorDensity) {
  if (density === "compact") return "p-3"
  if (density === "spacious") return "p-6"
  return "p-4"
}

function radiusClassName(radius: LemmaMarkdownEditorRadius, target: "surface" | "control") {
  if (radius === "none") return "rounded-none"
  if (radius === "sm") return target === "surface" ? "rounded-md" : "rounded-sm"
  if (radius === "md") return target === "surface" ? "rounded-lg" : "rounded-md"
  if (radius === "xl") return target === "surface" ? "rounded-2xl" : "rounded-xl"
  return target === "surface" ? "rounded-xl" : "rounded-lg"
}
