import { NextRequest } from "next/server";
import { handleConsentDecision } from "@/lib/consent-route";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  return handleConsentDecision(request, (await params).id, "approve");
}
