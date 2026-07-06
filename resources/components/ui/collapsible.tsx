// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import * as CollapsiblePrimitive from "@radix-ui/react-collapsible"

function Collapsible({ ...props }: React.ComponentProps<typeof CollapsiblePrimitive.Root>) {
	return <CollapsiblePrimitive.Root data-slot="collapsible" {...props} />
}

function CollapsibleTrigger({ ...props }: React.ComponentProps<typeof CollapsiblePrimitive.CollapsibleTrigger>) {
	return <CollapsiblePrimitive.CollapsibleTrigger data-slot="collapsible-trigger" {...props} />
}

function CollapsibleContent({ ...props }: React.ComponentProps<typeof CollapsiblePrimitive.CollapsibleContent>) {
	return <CollapsiblePrimitive.CollapsibleContent data-slot="collapsible-content" {...props} />
}

export { Collapsible, CollapsibleContent, CollapsibleTrigger }
