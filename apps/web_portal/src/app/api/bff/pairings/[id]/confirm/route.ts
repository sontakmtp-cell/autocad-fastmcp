import { NextRequest, NextResponse } from "next/server";
import { portalEnv } from "@/lib/env";
import { GatewayClient, GatewayError } from "@/lib/gateway-client";
import { requireSafeMutation } from "@/lib/security";
import { requireSession } from "@/lib/session";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const session = await requireSession();
    await requireSafeMutation(request, session);
    await new GatewayClient(session).confirmPairing((await params).id);
    return NextResponse.redirect(new URL("/pair?result=confirmed", portalEnv().PORTAL_PUBLIC_ORIGIN), 303);
  } catch (error) {
    const status = error instanceof GatewayError ? error.status : 400;
    return new NextResponse(status === 404 ? "Không tìm thấy" : "Yêu cầu không hợp lệ", { status });
  }
}
