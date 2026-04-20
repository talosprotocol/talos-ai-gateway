import { McpClient } from './../talos-sdk-ts/packages/client/src/mcp-client';

async function main() {
  const GATEWAY_URL = process.env.TALOS_GATEWAY_URL || 'http://127.0.0.1:8001';
  const ADMIN_SECRET = process.env.AUTH_ADMIN_SECRET || 'dev-admin-secret';
  const ADMIN_PRINCIPAL = process.env.AUTH_ADMIN_PRINCIPAL || 'dev-admin';
  const DATA_PLANE_TOKEN = process.env.TALOS_API_TOKEN || 'test-key-hard';

  console.log('--- Talos SDK Interop Test ---');
  console.log(`Gateway: ${GATEWAY_URL}`);

  const tokenResponse = await fetch(`${GATEWAY_URL}/admin/v1/auth/token`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Talos-Admin-Secret': ADMIN_SECRET
    },
    body: JSON.stringify({
      principal: ADMIN_PRINCIPAL,
      permissions: ['mcp.read'],
      data_plane_token: DATA_PLANE_TOKEN,
      ttl_seconds: 3600
    })
  });
  if (!tokenResponse.ok) {
    throw new Error(`Failed to mint session token: ${tokenResponse.status} ${await tokenResponse.text()}`);
  }
  const { token } = await tokenResponse.json() as { token: string };

  const client = new McpClient(GATEWAY_URL, token);

  try {
    console.log('\n[TEST] Listing Servers...');
    const servers = await client.listServers();
    console.log(`Found ${servers.length} servers:`);
    servers.forEach(s => console.log(` - ${s.id}: ${s.name}`));

    if (servers.length > 0) {
      const firstServer = servers[0].id;
      console.log(`\n[TEST] Listing Tools for ${firstServer}...`);
      const tools = await client.listTools(firstServer);
      console.log(`Found ${tools.length} tools:`);
      tools.slice(0, 3).forEach(t => console.log(` - ${t.name}: ${t.description?.substring(0, 50)}...`));
    }

    console.log('\n[SUCCESS] SDK Interop Verified!');
  } catch (err) {
    console.error('\n[FAILURE] SDK Interop failed:');
    console.error(err);
    process.exit(1);
  }
}

main();
