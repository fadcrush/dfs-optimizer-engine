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

// FanDuel slot order for display
const FD_SLOTS = ['PG', 'PG_2', 'SG', 'SG_2', 'SF', 'SF_2', 'PF', 'PF_2', 'C'] as const

type SlotKey = (typeof FD_SLOTS)[number]

export default function OptimizerPage() {
  // Form state
  const [file, setFile] = useState<File | null>(null)
  const [numLineups, setNumLineups] = useState(20)
  const [minSalary, setMinSalary] = useState(59000)

  // UI state
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Results state
  const [result, setResult] = useState<FDOptimizerResponse | null>(null)

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0]
    if (selected) {
      if (!selected.name.endsWith('.csv')) {
        setError('Please select a CSV file')
        setFile(null)
        return
      }
      setFile(selected)
      setError(null)
    }
  }

  const handleRunOptimizer = async () => {
    if (!file) {
      setError('Please select a CSV file first')
      return
    }

    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const response = await runFDOptimizer(file, {
        numLineups,
        minSalary,
        maxSalary: 60000,
        maxExposure: 0.35,
      })

      if (!response.success) {
        setError(response.error || 'Optimization failed')
        return
      }

      if (response.data) {
        setResult(response.data)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An unexpected error occurred')
    } finally {
      setLoading(false)
    }
  }

  const handleDownloadCSV = async () => {
    if (!result?.download_file) {
      setError('No file available for download')
      return
    }

    try {
      const response = await downloadLineups(result.download_file)
      if (response.success && response.data) {
        downloadFile(response.data, result.download_file)
      } else {
        setError(response.error || 'Download failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Download failed')
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
                CSV File
              </label>
              <input
                type="file"
                accept=".csv"
                onChange={handleFileChange}
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white file:mr-4 file:py-2 file:px-4 file:rounded file:border-0 file:bg-blue-600 file:text-white file:cursor-pointer"
              />
              {file && (
                <p className="text-sm text-green-400 mt-1">{file.name}</p>
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

          {/* Run Button */}
          <button
            onClick={handleRunOptimizer}
            disabled={loading || !file}
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
        {error && (
          <div className="bg-red-900/50 border border-red-500 rounded-lg p-4 mb-6">
            <p className="text-red-300">{error}</p>
          </div>
        )}

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
