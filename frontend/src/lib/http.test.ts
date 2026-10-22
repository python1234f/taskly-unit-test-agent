import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { request } from './http'

const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => vi.unstubAllGlobals())

it('returns JSON and merges request headers', async () => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: 7 })))
  await expect(
    request('/todos/7', { headers: { 'X-Request-ID': 'test-request' } }),
  ).resolves.toEqual({ id: 7 })
  expect(fetchMock).toHaveBeenCalledWith('/api/todos/7', {
    headers: {
      'Content-Type': 'application/json',
      'X-Request-ID': 'test-request',
    },
  })
})

it('returns undefined for an empty 204 response', async () => {
  fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
  await expect(
    request('/todos/7', { method: 'DELETE' }),
  ).resolves.toBeUndefined()
})

it('provides an actionable message for network failures', async () => {
  fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
  await expect(request('/todos')).rejects.toThrow(
    'Cannot connect to the server. Please try again.',
  )
})

it('preserves the original error when a request was aborted', async () => {
  const controller = new AbortController()
  const error = new DOMException('Request aborted', 'AbortError')
  controller.abort()
  fetchMock.mockRejectedValue(error)
  await expect(request('/todos', { signal: controller.signal })).rejects.toBe(
    error,
  )
})

it('explains validation failures instead of exposing Pydantic details', async () => {
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify({ detail: [{ type: 'string_too_long' }] }), {
      status: 422,
    }),
  )
  await expect(request('/todos')).rejects.toThrow(
    'Check the task details: title up to 120 characters, description up to 2000 characters, and a valid due date.',
  )
})

it('preserves a string error returned by the API', async () => {
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify({ detail: 'Task not found.' }), {
      status: 404,
    }),
  )
  await expect(request('/todos/404')).rejects.toThrow('Task not found.')
})

it('handles a server error whose body is not JSON', async () => {
  fetchMock.mockResolvedValue(
    new Response('<h1>Unavailable</h1>', { status: 503 }),
  )
  await expect(request('/todos')).rejects.toThrow(
    'The operation failed. Please try again.',
  )
})
