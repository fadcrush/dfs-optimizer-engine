/**
 * Tests for runOptimizerAsync — API client function that submits a job to
 * POST /api/optimizer/run-async and returns a task ID for polling.
 */

// Minimal global stubs so the module can load without a browser runtime.
// Suppress @/lib/auth import side-effects.
jest.mock('@/lib/auth', () => ({
  authFetch: jest.fn(),
  getApiErrorMessage: jest.fn().mockImplementation(async (res: Response) => res.statusText),
}))

import { runOptimizerAsync, type AsyncOptimizerSubmitResponse } from '@/lib/api'
import { authFetch, getApiErrorMessage } from '@/lib/auth'

const mockFetch = authFetch as jest.MockedFunction<typeof authFetch>
const mockGetError = getApiErrorMessage as jest.MockedFunction<typeof getApiErrorMessage>

function makeFile(name = 'slate.csv') {
  return new File(['Name,Pos,Team,Salary,Projection\n'], name, { type: 'text/csv' })
}

function mockOkResponse(body: AsyncOptimizerSubmitResponse): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as unknown as Response
}

function mockErrorResponse(status: number, text: string): Response {
  return {
    ok: false,
    status,
    statusText: text,
  } as unknown as Response
}

beforeEach(() => {
  jest.clearAllMocks()
  // Suppress console.error noise from URL constructor in test env
  process.env.NEXT_PUBLIC_API_BASE_URL = 'http://localhost:8000'
})

describe('runOptimizerAsync', () => {
  it('returns taskId on successful 200 response', async () => {
    mockFetch.mockResolvedValue(mockOkResponse({ task_id: 'abc-123', status: 'queued' }))

    const file = makeFile()
    const result = await runOptimizerAsync(file, {
      numLineups: 20,
      maxExposure: 0.5,
      site: 'FD',
    })

    expect(result.success).toBe(true)
    expect(result.taskId).toBe('abc-123')
    expect(result.error).toBeUndefined()
  })

  it('posts to /api/optimizer/run-async with correct query params', async () => {
    mockFetch.mockResolvedValue(mockOkResponse({ task_id: 'xyz', status: 'queued' }))

    const file = makeFile()
    await runOptimizerAsync(file, {
      numLineups: 5,
      maxExposure: 0.6,
      site: 'DK',
      numUnique: 3,
      contestType: 'cash',
    })

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const calledUrl = mockFetch.mock.calls[0][0] as string
    expect(calledUrl).toContain('/api/optimizer/run-async')
    expect(calledUrl).toContain('n_lineups=5')
    expect(calledUrl).toContain('max_exposure=0.6')
    expect(calledUrl).toContain('site=DK')
    expect(calledUrl).toContain('num_unique=3')
    expect(calledUrl).toContain('contest_type=cash')
  })

  it('forwards optional params when provided', async () => {
    mockFetch.mockResolvedValue(mockOkResponse({ task_id: 'opt1', status: 'queued' }))

    await runOptimizerAsync(makeFile(), {
      numLineups: 10,
      maxExposure: 0.5,
      outTeams: ['LAL', 'BOS'],
      outPlayers: ['Player X'],
      chalkThreshold: 30,
      projectionOverrides: { 'Player Y': 42 },
      lockedPlayers: ['Player Z'],
    })

    const calledUrl = mockFetch.mock.calls[0][0] as string
    expect(calledUrl).toContain('out_teams=LAL%2CBOS')
    expect(calledUrl).toContain('out_players=Player+X')
    expect(calledUrl).toContain('chalk_threshold=30')
    expect(calledUrl).toContain('projection_overrides=')
    expect(calledUrl).toContain('locked_players=Player+Z')
  })

  it('returns planGated when backend returns 403', async () => {
    mockFetch.mockResolvedValue(mockErrorResponse(403, 'Forbidden'))
    mockGetError.mockResolvedValue('Pro plan required')

    const result = await runOptimizerAsync(makeFile(), { numLineups: 1, maxExposure: 0.5 })

    expect(result.success).toBe(false)
    expect(result.planGated).toBe(true)
    expect(result.error).toBe('Pro plan required')
  })

  it('returns error message when backend returns 503 (Celery unavailable)', async () => {
    mockFetch.mockResolvedValue(mockErrorResponse(503, 'Service Unavailable'))
    mockGetError.mockResolvedValue('Background task queue unavailable')

    const result = await runOptimizerAsync(makeFile(), { numLineups: 1, maxExposure: 0.5 })

    expect(result.success).toBe(false)
    expect(result.planGated).toBe(false)
    expect(result.error).toBe('Background task queue unavailable')
  })

  it('returns success: false when fetch itself throws (network error)', async () => {
    mockFetch.mockRejectedValue(new Error('Network error'))

    await expect(
      runOptimizerAsync(makeFile(), { numLineups: 1, maxExposure: 0.5 })
    ).rejects.toThrow('Network error')
  })
})
