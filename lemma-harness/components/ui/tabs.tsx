"use client"

import * as React from "react"
import * as TabsPrimitive from "@radix-ui/react-tabs"
import { cn } from "@/lib/utils"

const Tabs = TabsPrimitive.Root

const TabsList = React.forwardRef<
    React.ElementRef<typeof TabsPrimitive.List>,
    React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
	<TabsPrimitive.List
	    ref={ref}
	    className={cn(
	        "inline-flex h-8 items-center justify-center rounded-md border border-[color:var(--chip-border)] bg-[var(--chip-bg)] p-0.5 text-[var(--text-tertiary)] shadow-none",
	        className
	    )}
        {...props}
    />
))
TabsList.displayName = TabsPrimitive.List.displayName

const TabsTrigger = React.forwardRef<
    React.ElementRef<typeof TabsPrimitive.Trigger>,
    React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
	<TabsPrimitive.Trigger
	    ref={ref}
	    className={cn(
	        "inline-flex h-7 items-center justify-center whitespace-nowrap rounded-sm px-2.5 py-1 text-sm font-medium transition-gentle focus-visible:outline-none focus-ring disabled:pointer-events-none disabled:opacity-50 data-[state=active]:bg-[var(--surface-1)] data-[state=active]:text-[var(--text-primary)] data-[state=active]:shadow-[var(--shadow-xs)]",
	        className
	    )}
        {...props}
    />
))
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName

const TabsContent = React.forwardRef<
    React.ElementRef<typeof TabsPrimitive.Content>,
    React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
    <TabsPrimitive.Content
        ref={ref}
        className={cn(
            "mt-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--ring)]",
            className
        )}
        {...props}
    />
))
TabsContent.displayName = TabsPrimitive.Content.displayName

export { Tabs, TabsList, TabsTrigger, TabsContent }
