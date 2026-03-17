export async function resolveMock<T>(mockData: T, realCall: () => Promise<T>) {
  try {
    const data = await realCall()
    return { data, isMock: false }
  } catch {
    return { data: mockData, isMock: true }
  }
}
