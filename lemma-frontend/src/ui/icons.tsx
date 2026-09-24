export {
    DownloadSimple as DownloadIcon, DotsThree as MoreIcon, GearSix as SettingsIcon, SidebarSimple as SidebarIcon, List as MenuIcon,
    MagnifyingGlass as SearchIcon, Plus as PlusIcon, X as CloseIcon,
    CaretDown as ChevronDownIcon, CaretRight as ChevronRightIcon, CaretUp as ChevronUpIcon, CaretLeft as ChevronLeftIcon,
    ArrowUp as SendIcon, ArrowUpRight as ExternalIcon, ArrowLeft as BackIcon,
    ArrowRight as ArrowRightIcon, Check as CheckIcon, Copy as CopyIcon,
    EnvelopeSimple as EmailIcon, LinkSimple as LinkIcon, UserCircle as UserIcon,
    SignOut as SignOutIcon, ArrowClockwise as RefreshIcon, ChatCircle as ChatIcon,
    IdentificationCard as ProfileIcon, ClockCounterClockwise as HistoryIcon,
    SquaresFour as AppsIcon, Folder as FolderIcon, Books as LibraryIcon, FileText as FileIcon, Table as TableIcon,
    Robot as AgentIcon, Code as CodeIcon, FlowArrow as WorkflowIcon,
    Clock as ClockIcon, Waveform as VoiceIcon, Stop as StopIcon,
    Microphone as MicIcon, MicrophoneSlash as MicOffIcon, PhoneDisconnect as EndCallIcon,
    CornersIn as MinimizeIcon, CornersOut as ExpandIcon,
    Sun as SunIcon, Moon as MoonIcon, Desktop as SystemIcon, Palette as AppearanceIcon,
    CheckCircle as CheckCircleIcon, WarningCircle as WarningIcon,
    DiscordLogo as DiscordIcon, Paperclip as AttachIcon, Archive as ArchiveIcon, Bell as BellIcon,
    ShieldCheck as ShieldIcon, Question as QuestionIcon, Prohibit as DenyIcon,
    Key as KeyIcon, TerminalWindow as TerminalIcon, Sparkle as SparkleIcon, Laptop as ComputerIcon,
    Users as PeopleIcon, ChartBar as UsageIcon, PlugsConnected as ConnectorIcon, Buildings as OrgIcon,
    CreditCard as CardIcon, Receipt as ReceiptIcon, ArrowCircleUp as UpgradeIcon,
    Globe as GlobeIcon, Image as ImageIcon, LockSimple as LockIcon, PencilSimple as EditIcon,
    Browser as BrowserIcon, CursorClick as PointerIcon, TreeStructure as TreeIcon, TextAlignLeft as TextIcon,
    // A teammate's capabilities, drawn. `Brain` is memory, which is a
    // capability rather than a tool; `Toolbox` is the neutral mark for a
    // toolset this build has not been taught a name for yet.
    Brain as MemoryIcon, Toolbox as ToolIcon,
    // A file removed, and a file moved, on the cards for a local agent's edits.
    Trash as DeleteIcon, ArrowsLeftRight as MoveIcon,
} from "@phosphor-icons/react";

export function LemmaLogo({ compact = false }: { compact?: boolean }) {
    return <span className="lemma-logo" aria-label="Lemma">
        <span className="lemma-brand-mark" aria-hidden="true"><i /><i /><i /></span>
        {!compact && <span className="lemma-wordmark">Lemma</span>}
    </span>;
}

/** The three bars on their own, at whatever size the row around them uses.
 *
 *  For the models Lemma itself provides. Those drew a generic sparkle, which
 *  is the mark every product in this category puts on anything to do with a
 *  model — so it said nothing the row's own name had not already said, and it
 *  said it in the same hand as the third-party keys beside it. */
export function LemmaMark({ size = 15 }: { size?: number }) {
    return (
        <span className="lemma-brand-mark lemma-brand-mark--inline" style={{ height: size }} aria-hidden="true">
            <i /><i /><i />
        </span>
    );
}
