import { afterEach, describe, expect, it, vi } from 'vitest'
import { isOverdue, selectTodos, validateTodo } from './todos'
import type { Todo } from '../types'

const base: Todo = {
  id: 1,
  title: 'Sprint plan',
  description: '',
  priority: 'medium',
  completed: false,
  due_date: null,
  created_at: '2026-10-01T12:00:00Z',
  updated_at: '2026-10-01T12:00:00Z',
}

describe('selectTodos', () => {
  it('combines status with a case-insensitive search in the description', () => {
    const matching = { ...base, description: 'Discuss the API' }
    const todos = [
      matching,
      { ...matching, id: 2, completed: true },
      { ...base, id: 3 },
    ]
    expect(selectTodos(todos, 'active', ' api ', 'newest')).toEqual([matching])
  })

  it('sorts by priority without changing the source array', () => {
    const high: Todo = { ...base, id: 2, priority: 'high' }
    const todos = [base, high]
    expect(selectTodos(todos, 'all', '', 'priority')).toEqual([high, base])
    expect(todos).toEqual([base, high])
  })
})

afterEach(() => vi.useRealTimers())

it('accepts length boundaries and rejects values beyond them', () => {
  expect(
    validateTodo({
      ...base,
      title: 'x'.repeat(120),
      description: 'x'.repeat(2000),
    }),
  ).toEqual({})
  expect(
    validateTodo({
      ...base,
      title: 'x'.repeat(121),
      description: 'x'.repeat(2001),
    }),
  ).toEqual({
    title: 'The title must be at most 120 characters.',
    description: 'The description must be at most 2000 characters.',
  })
})

it('rejects impossible calendar dates and accepts a leap day', () => {
  expect(validateTodo({ ...base, due_date: '2026-02-30' })).toEqual({
    due_date: 'Enter a valid date.',
  })
  expect(validateTodo({ ...base, due_date: '2028-02-29' })).toEqual({})
})

it('marks only unfinished tasks before the local calendar date as overdue', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(2026, 9, 22, 12))
  expect(isOverdue({ ...base, due_date: '2026-10-21' })).toBe(true)
  expect(isOverdue({ ...base, due_date: '2026-10-22' })).toBe(false)
  expect(isOverdue({ ...base, due_date: '2026-10-21', completed: true })).toBe(
    false,
  )
  expect(isOverdue(base)).toBe(false)
})
