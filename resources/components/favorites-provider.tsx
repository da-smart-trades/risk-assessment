// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Certora

import axios from "axios"
import { createContext, type PropsWithChildren, useCallback, useContext, useEffect, useMemo, useState } from "react"
import type { DashboardSummary, Favorite } from "@/lib/generated/api/types.gen"

type AutoTarget = {
	kind: "auto"
	metricType: string
	chain?: string | null
	token?: string | null
}

type ManualTarget = {
	kind: "manual"
	manualMetricId: string
}

export type FavoriteTarget = AutoTarget | ManualTarget

interface FavoritesContextValue {
	loading: boolean
	/** The current user's own home pages (default first), the only boards they can pin to. */
	boards: DashboardSummary[]
	/** Pinned favorites keyed by board id. */
	favoritesByBoard: Record<string, Favorite[]>
	/** The favorite row for ``target`` on a specific board, if pinned there. */
	isFavoritedOn: (target: FavoriteTarget, boardId: string) => Favorite | undefined
	/** Whether ``target`` is pinned to any of the user's own boards. */
	isFavoritedAnywhere: (target: FavoriteTarget) => boolean
	/** Pin/unpin ``target`` on a specific owned board. */
	toggleOn: (target: FavoriteTarget, boardId: string) => Promise<void>
	/** Create a new home page and immediately pin ``target`` to it. */
	createBoardAndPin: (name: string, target: FavoriteTarget) => Promise<void>
	/** Re-pull boards + favorites from the server. Use after a mutation that
	 * happened outside this provider (e.g. DashboardSwitcher's create / rename /
	 * delete) to keep the persistent-layout state in sync. */
	refetch: () => Promise<void>
}

const FavoritesContext = createContext<FavoritesContextValue | null>(null)

function targetMatchesFavorite(target: FavoriteTarget, fav: Favorite): boolean {
	if (target.kind === "manual") {
		return fav.manualMetricId === target.manualMetricId
	}
	return fav.metricType === target.metricType && (fav.chain ?? null) === (target.chain ?? null) && (fav.token ?? null) === (target.token ?? null)
}

/**
 * Tracks which targets are pinned to each of the user's *own* home pages and
 * toggles them per board. The star buttons live on chain/metric pages; clicking
 * one opens a picker so a metric can be pinned to any specific board (not just
 * the default). Shared-with-me boards are not pinnable and are excluded here.
 */
export function FavoritesProvider({ children }: PropsWithChildren) {
	const [boards, setBoards] = useState<DashboardSummary[]>([])
	const [favoritesByBoard, setFavoritesByBoard] = useState<Record<string, Favorite[]>>({})
	const [loading, setLoading] = useState(true)

	const fetchAll = useCallback(async (signal?: AbortSignal) => {
		setLoading(true)
		try {
			const { data: dashboards } = await axios.get<DashboardSummary[]>("/api/dashboards", { signal })
			const owned = dashboards.filter((d) => d.isOwner)
			const entries = await Promise.all(
				owned.map(async (d) => {
					const { data: items } = await axios.get<Favorite[]>(`/api/dashboards/${d.id}/favorites`, { signal })
					return [d.id, items] as const
				}),
			)
			if (signal?.aborted) return
			setBoards(owned)
			setFavoritesByBoard(Object.fromEntries(entries))
		} catch (err) {
			if (axios.isCancel(err)) return
			setBoards([])
			setFavoritesByBoard({})
		} finally {
			if (!signal?.aborted) setLoading(false)
		}
	}, [])

	useEffect(() => {
		const controller = new AbortController()
		void fetchAll(controller.signal)
		return () => controller.abort()
	}, [fetchAll])

	const refetch = useCallback(() => fetchAll(), [fetchAll])

	const isFavoritedOn = useCallback(
		(target: FavoriteTarget, boardId: string): Favorite | undefined => (favoritesByBoard[boardId] ?? []).find((f) => targetMatchesFavorite(target, f)),
		[favoritesByBoard],
	)

	const isFavoritedAnywhere = useCallback(
		(target: FavoriteTarget): boolean => Object.values(favoritesByBoard).some((items) => items.some((f) => targetMatchesFavorite(target, f))),
		[favoritesByBoard],
	)

	const toggleOn = useCallback(
		async (target: FavoriteTarget, boardId: string) => {
			const existing = (favoritesByBoard[boardId] ?? []).find((f) => targetMatchesFavorite(target, f))
			if (existing) {
				await axios.delete(`/api/dashboards/${boardId}/favorites/${existing.id}`)
				setFavoritesByBoard((prev) => ({ ...prev, [boardId]: (prev[boardId] ?? []).filter((f) => f.id !== existing.id) }))
				return
			}
			const res =
				target.kind === "manual"
					? await axios.post<Favorite>(`/api/dashboards/${boardId}/favorites/manual`, { manualMetricId: target.manualMetricId })
					: await axios.post<Favorite>(`/api/dashboards/${boardId}/favorites/auto`, {
							metricType: target.metricType,
							chain: target.chain ?? null,
							token: target.token ?? null,
						})
			setFavoritesByBoard((prev) => ({ ...prev, [boardId]: [...(prev[boardId] ?? []), res.data] }))
		},
		[favoritesByBoard],
	)

	const createBoardAndPin = useCallback(
		async (name: string, target: FavoriteTarget) => {
			const { data: board } = await axios.post<DashboardSummary>("/api/dashboards", { name })
			setBoards((prev) => (prev.some((b) => b.id === board.id) ? prev : [...prev, board]))
			setFavoritesByBoard((prev) => ({ ...prev, [board.id]: prev[board.id] ?? [] }))
			await toggleOn(target, board.id)
		},
		[toggleOn],
	)

	const value = useMemo(
		() => ({ loading, boards, favoritesByBoard, isFavoritedOn, isFavoritedAnywhere, toggleOn, createBoardAndPin, refetch }),
		[loading, boards, favoritesByBoard, isFavoritedOn, isFavoritedAnywhere, toggleOn, createBoardAndPin, refetch],
	)

	return <FavoritesContext.Provider value={value}>{children}</FavoritesContext.Provider>
}

export function useFavorites(): FavoritesContextValue {
	const ctx = useContext(FavoritesContext)
	if (ctx === null) {
		throw new Error("useFavorites must be used inside <FavoritesProvider>.")
	}
	return ctx
}
