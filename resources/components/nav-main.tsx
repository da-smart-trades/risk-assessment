// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import { Link } from "@inertiajs/react"
import { ChevronRight, type LucideIcon } from "lucide-react"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import {
	SidebarGroup,
	SidebarGroupLabel,
	SidebarMenu,
	SidebarMenuButton,
	SidebarMenuItem,
	SidebarMenuSub,
	SidebarMenuSubButton,
	SidebarMenuSubItem,
	useSidebar,
} from "@/components/ui/sidebar"

/** A leaf sub-item: a plain link, optionally with a small logo. */
interface NavLeaf {
	title: string
	href: string
	logo?: string
}

/**
 * A second-level group: an expandable sub-item that has its own leaf
 * children (e.g. a protocol under Markets that expands into markets).
 */
interface NavSubGroup {
	title: string
	logo?: string
	isActive?: boolean
	items: NavLeaf[]
}

type NavSubItem = NavLeaf | NavSubGroup

interface NavItem {
	title: string
	href: string
	icon?: LucideIcon
	isActive?: boolean
	items?: NavSubItem[]
}

function isSubGroup(item: NavSubItem): item is NavSubGroup {
	return Array.isArray((item as NavSubGroup).items)
}

export function NavMain({ items }: { items: NavItem[] }) {
	const { state } = useSidebar()
	const isCollapsed = state === "collapsed"

	return (
		<SidebarGroup>
			<SidebarGroupLabel>Platform</SidebarGroupLabel>
			<SidebarMenu>
				{items.map((item) => {
					const hasChildren = item.items && item.items.length > 0

					if (!hasChildren || isCollapsed) {
						return (
							<SidebarMenuItem key={item.title}>
								<SidebarMenuButton asChild isActive={item.isActive} tooltip={item.title}>
									<Link href={item.href}>
										{item.icon && <item.icon className="shrink-0" />}
										<span className="group-data-[collapsible=icon]:hidden">{item.title}</span>
									</Link>
								</SidebarMenuButton>
							</SidebarMenuItem>
						)
					}

					return (
						<Collapsible key={item.title} asChild defaultOpen={item.isActive} className="group/collapsible">
							<SidebarMenuItem>
								<CollapsibleTrigger asChild>
									<SidebarMenuButton tooltip={item.title} isActive={item.isActive}>
										{item.icon && <item.icon className="shrink-0" />}
										<span className="group-data-[collapsible=icon]:hidden">{item.title}</span>
										<ChevronRight className="ml-auto transition-transform duration-200 group-data-[state=open]/collapsible:rotate-90 group-data-[collapsible=icon]:hidden" />
									</SidebarMenuButton>
								</CollapsibleTrigger>
								<CollapsibleContent>
									<SidebarMenuSub>
										{item.items?.map((subItem) =>
											isSubGroup(subItem) ? <NavSubGroupItem key={subItem.title} group={subItem} /> : <NavLeafItem key={subItem.href} leaf={subItem} />,
										)}
									</SidebarMenuSub>
								</CollapsibleContent>
							</SidebarMenuItem>
						</Collapsible>
					)
				})}
			</SidebarMenu>
		</SidebarGroup>
	)
}

/** A second-level expandable group (e.g. a protocol or chain). */
function NavSubGroupItem({ group, depth = 1 }: { group: NavSubGroup; depth?: number }) {
	// Each SidebarMenuSub nesting adds mx-3.5 + px-2.5 of indent, which eats
	// horizontal room fast under the deep Markets → protocol → chain → market
	// tree. Tighten the margin/padding at every nested level (and tighter
	// still at depth 2, the chain level whose children are market leaves) so
	// the leaf labels get ~20 characters of width before truncating.
	const innerSubClassName = depth >= 2 ? "mx-0.5 px-1" : "mx-1 px-1.5"
	return (
		<Collapsible asChild defaultOpen={group.isActive} className="group/subcollapsible">
			<SidebarMenuSubItem>
				<CollapsibleTrigger asChild>
					<SidebarMenuSubButton className="cursor-pointer">
						{group.logo && <img src={group.logo} alt="" aria-hidden width={16} height={16} className="shrink-0 rounded-full object-contain" />}
						<span className="truncate">{group.title}</span>
						<ChevronRight className="ml-auto transition-transform duration-200 group-data-[state=open]/subcollapsible:rotate-90" />
					</SidebarMenuSubButton>
				</CollapsibleTrigger>
				<CollapsibleContent>
					<SidebarMenuSub className={innerSubClassName}>
						{group.items.map((child) =>
							isSubGroup(child) ? <NavSubGroupItem key={child.title} group={child} depth={depth + 1} /> : <NavLeafItem key={child.href} leaf={child} />,
						)}
					</SidebarMenuSub>
				</CollapsibleContent>
			</SidebarMenuSubItem>
		</Collapsible>
	)
}

function NavLeafItem({ leaf }: { leaf: NavLeaf }) {
	return (
		<SidebarMenuSubItem>
			<SidebarMenuSubButton asChild>
				<Link href={leaf.href} className="flex items-center gap-2">
					{leaf.logo && <img src={leaf.logo} alt="" aria-hidden width={16} height={16} className="shrink-0 rounded-full object-contain" />}
					<span className="min-w-[20ch] truncate">{leaf.title}</span>
				</Link>
			</SidebarMenuSubButton>
		</SidebarMenuSubItem>
	)
}
