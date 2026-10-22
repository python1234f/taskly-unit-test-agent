import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { todoFixture } from '../../../test/todoFixture'
import { todoKeys } from '../api/queryKeys'
import { todosApi } from '../api/todos'
import { useCreateTodoMutation } from './useCreateTodoMutation'
import { useDeleteTodoMutation } from './useDeleteTodoMutation'
import { useUpdateTodoMutation } from './useUpdateTodoMutation'

vi.mock('../api/todos', () => ({
  todosApi: { create: vi.fn(), update: vi.fn(), remove: vi.fn() },
}))

const clients: QueryClient[] = []

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  clients.push(client)
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return { client, wrapper }
}

afterEach(() => {
  clients.splice(0).forEach((client) => client.clear())
  vi.clearAllMocks()
})

it('stores the confirmed created task without duplicating its ID', async () => {
  const { client, wrapper } = setup()
  const saved = { ...todoFixture, title: 'Confirmed title' }
  client.setQueryData(todoKeys.list, [todoFixture])
  vi.mocked(todosApi.create).mockResolvedValue(saved)
  const { result } = renderHook(() => useCreateTodoMutation(), { wrapper })
  await act(async () => {
    await result.current.mutateAsync(todoFixture)
  })
  expect(client.getQueryData(todoKeys.list)).toEqual([saved])
})

it('updates only the matching cached task with the confirmed server value', async () => {
  const { client, wrapper } = setup()
  const other = { ...todoFixture, id: 8 }
  const saved = { ...todoFixture, completed: true }
  client.setQueryData(todoKeys.list, [todoFixture, other])
  vi.mocked(todosApi.update).mockResolvedValue(saved)
  const { result } = renderHook(() => useUpdateTodoMutation(), { wrapper })
  await act(async () => {
    await result.current.mutateAsync({
      id: todoFixture.id,
      input: { completed: true },
    })
  })
  expect(client.getQueryData(todoKeys.list)).toEqual([saved, other])
})

it('preserves the cache on deletion failure and removes the task after retry', async () => {
  const { client, wrapper } = setup()
  const other = { ...todoFixture, id: 8 }
  const error = new Error('Deletion unavailable')
  client.setQueryData(todoKeys.list, [todoFixture, other])
  vi.mocked(todosApi.remove).mockRejectedValueOnce(error)
  const { result } = renderHook(() => useDeleteTodoMutation(), { wrapper })
  await act(async () => {
    await expect(result.current.mutateAsync(todoFixture.id)).rejects.toBe(error)
  })
  expect(client.getQueryData(todoKeys.list)).toEqual([todoFixture, other])
  vi.mocked(todosApi.remove).mockResolvedValue(undefined)
  await act(async () => {
    await result.current.mutateAsync(todoFixture.id)
  })
  expect(client.getQueryData(todoKeys.list)).toEqual([other])
})
