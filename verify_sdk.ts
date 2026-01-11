import { McpClient } from './../talos-sdk-ts/packages/client/src/mcp-client';

async function main() {
  const GATEWAY_URL = 'http://127.0.0.1:8001';
  const API_TOKEN = 'sk-test-key-1'; // Valid mock key from auth_public.py

  console.log('--- Talos SDK Interop Test ---');
  console.log(`Gateway: ${GATEWAY_URL}`);

  const client = new McpClient(GATEWAY_URL, API_TOKEN);

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
