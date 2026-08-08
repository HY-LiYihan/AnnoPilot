export async function responseMessage(response: Response) {
  try {
    const payload = await response.json()
    return payload.detail ?? response.statusText
  } catch {
    return response.statusText
  }
}

export async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(await responseMessage(response))
  return response.json() as Promise<T>
}
