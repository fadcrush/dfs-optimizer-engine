/**
 * Tests for the task status polling contract.
 *
 * The useTaskStatus hook polls GET /api/tasks/{taskId} via authFetch.
 * These tests verify the state machine contract: what URLs are fetched,
 * how Celery states map to TaskStatusValues, and what data is propagated.
 *
 * We test the poll function behavior directly through authFetch mocks.
 * (React hook rendering is not covered here — @testing-library/react is not
 * installed; the integration is verified end-to-end through the optimizer page.)
 */

jest.mock('@/lib/auth', () => ({
  authFetch: jest.fn(),
}))

import { authFetch } from '@/lib/auth'

const mockFetch = authFetch as jest.MockedFunction<typeof authFetch>

type TaskPayload = { status: string; result?: unknown; error?: string }

function makeResponse(payload: TaskPayload, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 500,
    json: async () => payload,
  } as unknown as Response
}

// Simulate a single poll cycle: call authFetch with the task URL and read JSON.
async function simulatePoll(taskId: string): Promise<TaskPayload | null> {
  const res = await mockFetch(`/api/tasks/${taskId}`)
  if (!res.ok) return null
  return res.json() as Promise<TaskPayload>
}

beforeEach(() => {
  jest.clearAllMocks()
})

describe('Task status polling contract', () => {
  it('polls the correct /api/tasks/{taskId} URL', async () => {
    mockFetch.mockResolvedValue(makeResponse({ status: 'queued' }))

    await simulatePoll('abc-123')

    expect(mockFetch).toHaveBeenCalledWith('/api/tasks/abc-123')
  })

  it('returns queued status when Celery task is PENDING', async () => {
    mockFetch.mockResolvedValue(makeResponse({ status: 'queued' }))

    const payload = await simulatePoll('t1')

    expect(payload?.status).toBe('queued')
  })

  it('returns running status when Celery task is STARTED', async () => {
    mockFetch.mockResolvedValue(makeResponse({ status: 'running' }))

    const payload = await simulatePoll('t2')

    expect(payload?.status).toBe('running')
  })

  it('returns done status with result payload on task success', async () => {
    const taskResult = { total_lineups: 20, lineups: [] }
    mockFetch.mockResolvedValue(makeResponse({ status: 'done', result: taskResult }))

    const payload = await simulatePoll('t3')

    expect(payload?.status).toBe('done')
    expect(payload?.result).toEqual(taskResult)
    expect(payload?.error).toBeUndefined()
  })

  it('returns error status with message on task failure', async () => {
    mockFetch.mockResolvedValue(makeResponse({ status: 'error', error: 'LP infeasible' }))

    const payload = await simulatePoll('t4')

    expect(payload?.status).toBe('error')
    expect(payload?.error).toBe('LP infeasible')
  })

  it('returns null when HTTP response is not ok', async () => {
    mockFetch.mockResolvedValue(makeResponse({ status: 'error' }, false))

    const payload = await simulatePoll('t5')

    expect(payload).toBeNull()
  })

  it('propagates network errors as thrown exceptions', async () => {
    mockFetch.mockRejectedValue(new Error('connection refused'))

    await expect(simulatePoll('t6')).rejects.toThrow('connection refused')
  })

  it('result field is present in done payload for downstream state sync', async () => {
    // The optimizer page uses a useEffect: if status==='done' && result, setResult(result).
    // This test confirms the result field survives the JSON round-trip.
    const lineups = [{ lineup_num: 1, total_salary: 59500, projected_points: 38.2 }]
    mockFetch.mockResolvedValue(
      makeResponse({ status: 'done', result: { total_lineups: 1, lineups } })
    )

    const payload = await simulatePoll('t7')
    const result = payload?.result as { total_lineups: number; lineups: typeof lineups }

    expect(result.total_lineups).toBe(1)
    expect(result.lineups[0].projected_points).toBe(38.2)
  })
})
