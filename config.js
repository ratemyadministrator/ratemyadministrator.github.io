// Supabase connection for the live site.
// The publishable key is safe to publish: the database rules in supabase/schema.sql
// limit it to reading visible reviews and adding new ones.
// NEVER put the secret key (sb_secret_...) in this file.
window.RMA_CONFIG = {
  supabaseUrl: "https://hzrzeavbnywrvstwnueb.supabase.co",
  supabasePublishableKey: "sb_publishable_W14MUFzOSVBfDMCCQTa9rw_ROBMJz35"
};
