"use client"

import Image from "next/image"
import * as React from "react"
import { cn } from "@/lib/utils"

const Avatar = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
    <div
        ref={ref}
        className={cn(
            "relative flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)] shadow-none",
            className
        )}
        {...props}
    />
))
Avatar.displayName = "Avatar"

type AvatarImageProps = Omit<React.ComponentPropsWithoutRef<typeof Image>, "alt" | "src"> & {
    alt?: string
    src?: string | null
}

const AvatarImage = React.forwardRef<HTMLImageElement, AvatarImageProps>(
    ({ className, alt = "", src, width = 40, height = 40, ...props }, ref) => {
        if (!src) return null

        return (
            <Image
                ref={ref}
                alt={alt}
                src={src}
                width={Number(width) || 40}
                height={Number(height) || 40}
                className={cn("aspect-square h-full w-full object-cover", className)}
                unoptimized
                {...props}
            />
        )
    }
)
AvatarImage.displayName = "AvatarImage"

const AvatarFallback = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
    <div
        ref={ref}
        className={cn(
            "flex h-full w-full items-center justify-center rounded-full bg-[var(--surface-3)] text-[var(--text-secondary)] text-xs font-medium",
            className
        )}
        {...props}
    />
))
AvatarFallback.displayName = "AvatarFallback"

export { Avatar, AvatarImage, AvatarFallback }
