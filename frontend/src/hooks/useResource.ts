import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/lib/api'

/** Thin wrappers over the REST endpoints; every resource follows the same shape. */
export function useList<T>(resource: string, params?: Record<string, unknown>) {
  return useQuery({
    queryKey: [resource, params],
    queryFn: () => api.get<T[]>(`/${resource}`, params as never),
  })
}

export function useItem<T>(resource: string, id: number | string | undefined) {
  return useQuery({
    queryKey: [resource, id],
    queryFn: () => api.get<T>(`/${resource}/${id}`),
    enabled: id !== undefined,
  })
}

export function useCreate<T, B = unknown>(resource: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: B) => api.post<T>(`/${resource}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: [resource] }),
  })
}

export function useRemove(resource: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.del(`/${resource}/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: [resource] }),
  })
}
