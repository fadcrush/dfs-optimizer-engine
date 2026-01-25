'use client'

import { useState } from 'react'
import {
  runFDOptimizer,
  downloadLineups,
  downloadFile,
  type FDOptimizerResponse,
  type FDLineup,
} from '@/lib/api'
import { formatSalary, formatProjection } from '@/lib/utils'

// ============================================================================
// Configuration
// ============================================================================

const MAX_FILE_SIZE_MB = 10
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

// Required CSV columns (case-insensitive, accepts common aliases)
const REQUIRED_COLUMNS = [
  { name: 'Name', aliases: ['name', 'player', 'nickname', 'playername', 'player_name'] },
  { name: 'Position', aliases: ['position', 'pos', 'roster position'] },
  { name: 'Team', aliases: ['team', 'teamabbrev', 'team_abbrev'] },
  { name: 'Salary', aliases: ['salary', 'sal'] },
  { name: 'Projection', aliases: ['projection', 'proj', 'fppg', 'fpts', 'points', 'avgpointspergame'] },
]

// FanDuel slot order for display
const FD_SLOTS = ['PG', 'PG_2', 'SG', 'SG_2', 'SF', 'SF_2', 'PF', 'PF_2', 'C'] as const

type SlotKey = (typeof FD_SLOTS)[number]

// ============================================================================
// CSV Validation
// ============================================================================

interface ValidationResult {
  valid: boolean
  error?: string
  missingColumns?: string[]
  foundColumns?: string[]
}

async function validateCSV(file: File): Promise<ValidationResult> {
  // Check file extension
  if (!file.name.toLowerCase().endsWith('.csv')) {
    return { valid: false, error: 'File must be a CSV (.csv extension)' }
  }

  // Check file size
  if (file.size > MAX_FILE_SIZE_BYTES) {
    return {
      valid: false,
      error: `File too large (${(file.size / 1024 / 1024).toFixed(1)}MB). Maximum size is ${MAX_FILE_SIZE_MB}MB.`,
    }
  }

  // Check file is not empty
  if (file.size === 0) {
    return { valid: false, error: 'File is empty' }
  }

  // Read and parse headers
  try {
    const headerLine = await readFirstLine(file)
    if (!headerLine.trim()) {
      return { valid: false, error: 'CSV file has no header row' }
    }

    const headers = parseCSVLine(headerLine).map((h) => h.toLowerCase().trim())
    const foundColumns: string[] = []
    const missingColumns: string[] = []

    for (const required of REQUIRED_COLUMNS) {
      const found = required.aliases.some((alias) => headers.includes(alias))
      if (found) {
        foundColumns.push(required.name)
      } else {
        missingColumns.push(required.name)
      }
    }

    if (missingColumns.length > 0) {
      return {
        valid: false,
        error: `Missing required columns: ${missingColumns.join(', ')}`,
        missingColumns,
        foundColumns,
      }
    }

    return { valid: true, foundColumns }
  } catch (err) {
    return {
      valid: false,
      error: `Could not read CSV: ${err instanceof Error ? err.message : 'Unknown error'}`,
    }
  }
}

function readFirstLine(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      const content = e.target?.result as string
      const firstLine = content.split(/\r?\n/)[0] || ''
      resolve(firstLine)
    }
    reader.onerror = () => reject(new Error('Failed to read file'))
    // Only read first 10KB for header detection
    reader.readAsText(file.slice(0, 10240))
  })
}

function parseCSVLine(line: string): string[] {
  const result: string[] = []
  let current = ''
  let inQuotes = false

  for (let i = 0; i < line.length; i++) {
    const char = line[i]
    if (char === '"') {
      inQuotes = !inQuotes
    } else if (char === ',' && !inQuotes) {
      result.push(current)
      current = ''
    } else {
      current += char
    }
  }
  result.push(current)
  return result
}

// ============================================================================
// Error Display Component
// ============================================================================

interface ErrorDisplayProps {
  message: string
  rawError?: string
}

function ErrorDisplay({ message, rawError }: ErrorDisplayProps) {
  const [showRaw, setShowRaw] = useState(false)

  return (
    <div className="bg-red-900/50 border border-red-500 rounded-lg p-4 mb-6">
      <div className="flex items-start gap-3">
        <svg
          className="w-5 h-5 text-red-400 mt-0.5 flex-shrink-0"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
          />
        </svg>
        <div className="flex-1">
          <p className="text-red-300 font-medium">{message}</p>
          {rawError && rawError !== message && (
            <div className="mt-2">
              <button
                onClick={() => setShowRaw(!showRaw)}
                className="text-sm text-red-400 hover:text-red-300 underline"
              >
                {showRaw ? 'Hide' : 'Show'} technical details
              </button>
              {showRaw && (
                <pre className="mt-2 p-2 bg-black/30 rounded text-xs text-red-200 overflow-x-auto">
                  {rawError}
                </pre>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// Run Parameters Component
// ============================================================================

interface RunParams {
  numLineups: number
  minSalary: number
  maxSalary: number
  maxExposure: number
  platform: string
  contestType: string
}

function RunParametersDisplay({ params }: { params: RunParams }) {
  return (
    <div className="bg-gray-700/50 rounded p-3 mb-4">
      <p className="text-sm text-gray-400 mb-2">Run Parameters</p>
      <div className="flex flex-wrap gap-3 text-sm">
        <span className="px-2 py-1 bg-gray-600 rounded">
          Platform: <strong>{params.platform}</strong>
        </span>
        <span className="px-2 py-1 bg-gray-600 rounded">
          Lineups: <strong>{params.numLineups}</strong>
        </span>
        <span className="px-2 py-1 bg-gray-600 rounded">
          Salary: <strong>{formatSalary(params.minSalary)}</strong> - <strong>{formatSalary(params.maxSalary)}</strong>
        </span>
        <span className="px-2 py-1 bg-gray-600 rounded">
          Max Exposure: <strong>{(params.maxExposure * 100).toFixed(0)}%</strong>
        </span>
        <span className="px-2 py-1 bg-gray-600 rounded">
          Contest: <strong>{params.contestType}</strong>
        </span>
      </div>
    </div>
  )
}

// ============================================================================
// Main Page Component
// ============================================================================

export default function OptimizerPage() {
  // Form state
  const [file, setFile] = useState<File | null>(null)
  const [numLineups, setNumLineups] = useState(20)
  const [minSalary, setMinSalary] = useState(59000)

  // UI state
  const [loading, setLoading] = useState(false)
  const [validating, setValidating] = useState(false)
  const [error, setError] = useState<{ message: string; raw?: string } | null>(null)
  const [validationStatus, setValidationStatus] = useState<string | null>(null)

  // Results state
  const [result, setResult] = useState<FDOptimizerResponse | null>(null)
  const [runParams, setRunParams] = useState<RunParams | null>(null)

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0]
    setError(null)
    setValidationStatus(null)
    setFile(null)
    setResult(null)
    setRunParams(null)

    if (!selected) return

    setValidating(true)

    try {
      const validation = await validateCSV(selected)

      if (!validation.valid) {
        setError({
          message: validation.error || 'Invalid CSV file',
          raw: validation.missingColumns
            ? `Missing: ${validation.missingColumns.join(', ')}\nFound: ${validation.foundColumns?.join(', ') || 'none'}`
            : undefined,
        })
        return
      }

      setFile(selected)
      setValidationStatus(`Valid CSV with ${validation.foundColumns?.length || 0} required columns`)
    } finally {
      setValidating(false)
    }
  }

  const handleRunOptimizer = async () => {
    if (!file) {
      setError({ message: 'Please select a valid CSV file first' })
      return
    }

    setLoading(true)
    setError(null)
    setResult(null)

    // Capture run parameters
    const params: RunParams = {
      numLineups,
      minSalary,
      maxSalary: 60000,
      maxExposure: 0.35,
      platform: 'FanDuel',
      contestType: 'small_gpp',
    }

    try {
      const response = await runFDOptimizer(file, {
        numLineups: params.numLineups,
        minSalary: params.minSalary,
        maxSalary: params.maxSalary,
        maxExposure: params.maxExposure,
      })

      if (!response.success) {
        setError({
          message: 'Optimization failed. Check your CSV format and try again.',
          raw: response.error,
        })
        return
      }

      if (response.data) {
        setResult(response.data)
        setRunParams(params)
      }
    } catch (err) {
      setError({
        message: 'An unexpected error occurred while optimizing.',
        raw: err instanceof Error ? err.message : String(err),
      })
    } finally {
      setLoading(false)
    }
  }

  const handleDownloadCSV = async () => {
    if (!result?.download_file) {
      setError({ message: 'No file available for download' })
      return
    }

    try {
      const response = await downloadLineups(result.download_file)
      if (response.success && response.data) {
        downloadFile(response.data, result.download_file)
      } else {
        setError({
          message: 'Download failed. Please try again.',
          raw: response.error,
        })
      }
    } catch (err) {
      setError({
        message: 'Download failed unexpectedly.',
        raw: err instanceof Error ? err.message : String(err),
      })
    }
  }

  return (
    <div className="min-h-screen bg-gray-900 text-white p-8">
      <div className="max-w-6xl mx-auto">
        <h1 className="text-3xl font-bold mb-8">FanDuel NBA Optimizer</h1>

        {/* Input Section */}
        <div className="bg-gray-800 rounded-lg p-6 mb-6">
          <h2 className="text-xl font-semibold mb-4">Upload Projections</h2>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
            {/* File Input */}
            <div>
              <label className="block text-sm text-gray-400 mb-2">
                CSV File <span className="text-gray-500">(max {MAX_FILE_SIZE_MB}MB)</span>
              </label>
              <input
                type="file"
                accept=".csv"
                onChange={handleFileChange}
                disabled={validating}
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white file:mr-4 file:py-2 file:px-4 file:rounded file:border-0 file:bg-blue-600 file:text-white file:cursor-pointer disabled:opacity-50"
              />
              {validating && (
                <p className="text-sm text-yellow-400 mt-1">Validating CSV...</p>
              )}
              {file && validationStatus && (
                <p className="text-sm text-green-400 mt-1">
                  {file.name} - {validationStatus}
                </p>
              )}
            </div>

            {/* Num Lineups */}
            <div>
              <label className="block text-sm text-gray-400 mb-2">
                Number of Lineups
              </label>
              <input
                type="number"
                min={1}
                max={150}
                value={numLineups}
                onChange={(e) => setNumLineups(Number(e.target.value))}
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white"
              />
            </div>

            {/* Min Salary */}
            <div>
              <label className="block text-sm text-gray-400 mb-2">
                Minimum Salary
              </label>
              <input
                type="number"
                min={0}
                max={60000}
                step={1000}
                value={minSalary}
                onChange={(e) => setMinSalary(Number(e.target.value))}
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white"
              />
            </div>
          </div>

          {/* Required Columns Help */}
          <details className="mb-4 text-sm">
            <summary className="text-gray-400 cursor-pointer hover:text-gray-300">
              Required CSV columns
            </summary>
            <div className="mt-2 p-3 bg-gray-700/50 rounded">
              <p className="text-gray-300 mb-2">Your CSV must include these columns (or common aliases):</p>
              <ul className="list-disc list-inside text-gray-400 space-y-1">
                {REQUIRED_COLUMNS.map((col) => (
                  <li key={col.name}>
                    <strong className="text-gray-300">{col.name}</strong>
                    <span className="text-gray-500"> ({col.aliases.join(', ')})</span>
                  </li>
                ))}
              </ul>
            </div>
          </details>

          {/* Run Button */}
          <button
            onClick={handleRunOptimizer}
            disabled={loading || !file || validating}
            className="px-6 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 disabled:cursor-not-allowed rounded font-semibold transition-colors"
          >
            {loading ? (
              <span className="flex items-center gap-2">
                <svg
                  className="animate-spin h-5 w-5"
                  viewBox="0 0 24 24"
                  fill="none"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                  />
                </svg>
                Optimizing...
              </span>
            ) : (
              'Run Optimizer'
            )}
          </button>
        </div>

        {/* Error Display */}
        {error && <ErrorDisplay message={error.message} rawError={error.raw} />}

        {/* Results Section */}
        {result && (
          <>
            {/* Stats Panel */}
            <div className="bg-gray-800 rounded-lg p-6 mb-6">
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-xl font-semibold">Results</h2>
                <button
                  onClick={handleDownloadCSV}
                  className="px-4 py-2 bg-green-600 hover:bg-green-700 rounded font-semibold transition-colors"
                >
                  Download CSV
                </button>
              </div>

              {/* Run Parameters Used */}
              {runParams && <RunParametersDisplay params={runParams} />}

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="bg-gray-700 rounded p-3">
                  <p className="text-sm text-gray-400">Total Lineups</p>
                  <p className="text-xl font-bold">{result.total_lineups}</p>
                </div>
                <div className="bg-gray-700 rounded p-3">
                  <p className="text-sm text-gray-400">Avg Projection</p>
                  <p className="text-xl font-bold">
                    {formatProjection(result.stats?.avg_projection ?? 0)}
                  </p>
                </div>
                <div className="bg-gray-700 rounded p-3">
                  <p className="text-sm text-gray-400">Avg Salary</p>
                  <p className="text-xl font-bold">
                    {formatSalary(result.stats?.avg_salary ?? 0)}
                  </p>
                </div>
                <div className="bg-gray-700 rounded p-3">
                  <p className="text-sm text-gray-400">Projection Range</p>
                  <p className="text-xl font-bold">
                    {result.stats?.projection_range ?? 'N/A'}
                  </p>
                </div>
              </div>

              {result.message && (
                <p className="mt-4 text-green-400">{result.message}</p>
              )}
            </div>

            {/* Lineups Table */}
            <div className="bg-gray-800 rounded-lg p-6">
              <h2 className="text-xl font-semibold mb-4">
                Lineup Preview (Top {result.lineups?.length ?? 0})
              </h2>

              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-700">
                      <th className="text-left py-3 px-2">#</th>
                      {FD_SLOTS.map((slot) => (
                        <th key={slot} className="text-left py-3 px-2">
                          {slot.replace('_2', '')}
                        </th>
                      ))}
                      <th className="text-right py-3 px-2">Salary</th>
                      <th className="text-right py-3 px-2">Proj</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.lineups?.map((lineup: FDLineup) => (
                      <tr
                        key={lineup.lineup_num}
                        className="border-b border-gray-700/50 hover:bg-gray-700/30"
                      >
                        <td className="py-2 px-2 text-gray-400">
                          {lineup.lineup_num}
                        </td>
                        {FD_SLOTS.map((slot) => (
                          <td key={slot} className="py-2 px-2">
                            {lineup[slot as SlotKey] || '-'}
                          </td>
                        ))}
                        <td className="py-2 px-2 text-right">
                          {formatSalary(lineup.total_salary)}
                        </td>
                        <td className="py-2 px-2 text-right text-green-400">
                          {formatProjection(lineup.projected_points)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
