export type AdminResult = {
  status: number;
  body: unknown;
};

export async function adminRequest(path: string, method: string, body?: unknown): Promise<AdminResult> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      credentials: "include",
      headers: {
        Accept: "application/json",
        "X-Urumi-Request": "1",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return { status: 0, body: { error_description: "The gateway could not be reached." } };
  }
  try {
    return { status: response.status, body: await response.json() };
  } catch {
    return { status: response.status, body: null };
  }
}

export function errorText(body: unknown): string {
  if (body && typeof body === "object" && "error_description" in body) {
    const description = body.error_description;
    if (typeof description === "string" && description.length > 0 && description.length < 200) {
      return description;
    }
  }
  return "The request was rejected.";
}
