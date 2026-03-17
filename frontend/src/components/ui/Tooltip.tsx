'use client'
import * as RadixTooltip from '@radix-ui/react-tooltip'

export function Tooltip({
  content,
  children,
}: {
  content: string
  children: React.ReactNode
}) {
  return (
    <RadixTooltip.Provider delayDuration={300}>
      <RadixTooltip.Root>
        <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content
            className="z-[300] px-2 py-1 text-xs rounded bg-surface-overlay border border-surface-border text-text-secondary shadow-card animate-fadeIn"
            sideOffset={4}
          >
            {content}
            <RadixTooltip.Arrow className="fill-surface-overlay" />
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  )
}
