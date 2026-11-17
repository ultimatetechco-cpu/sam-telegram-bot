<?php
// ========================================================
// Telegram AutoPost Pro - Hostinger LiteSpeed API Gateway
// Proxies all frontend API & media calls to the active Python backend
// ========================================================

// Suppress raw error reporting to keep JSON responses clean
error_reporting(0);
@ini_set('display_errors', '0');

// Hardened Security: Standardized response headers
header("X-Frame-Options: SAMEORIGIN");
header("X-Content-Type-Options: nosniff");
header("X-XSS-Protection: 1; mode=block");
header("Referrer-Policy: strict-origin-when-cross-origin");

// Encryption at Rest for Hostinger Cloud Vault (AES-256-CBC + HMAC-SHA256)
function vault_encrypt($data, $secret) {
    if (empty($data)) return $data;
    $key = hash('sha256', $secret . '_vault_encryption_key_v2', true);
    $iv = openssl_random_pseudo_bytes(16);
    $ciphertext = openssl_encrypt($data, 'AES-256-CBC', $key, OPENSSL_RAW_DATA, $iv);
    if ($ciphertext === false) return $data;
    $mac = hash_hmac('sha256', $iv . $ciphertext, $key, true);
    return 'ENC_V2:' . base64_encode($iv . $mac . $ciphertext);
}

function vault_decrypt($encryptedStr, $secret) {
    if (empty($encryptedStr)) return $encryptedStr;
    if (strpos($encryptedStr, 'ENC_V2:') !== 0) {
        return $encryptedStr; // Return plain text for backward compatibility
    }
    $raw = base64_decode(substr($encryptedStr, 7));
    if (strlen($raw) < 48) return null;
    $iv = substr($raw, 0, 16);
    $mac = substr($raw, 16, 32);
    $ciphertext = substr($raw, 48);
    $key = hash('sha256', $secret . '_vault_encryption_key_v2', true);
    $expectedMac = hash_hmac('sha256', $iv . $ciphertext, $key, true);
    if (!hash_equals($mac, $expectedMac)) return null;
    $plain = openssl_decrypt($ciphertext, 'AES-256-CBC', $key, OPENSSL_RAW_DATA, $iv);
    return $plain !== false ? $plain : null;
}

// Load backend target URL directly (bypassing OPcache)
$backend_url = "https://sam-telegram-bot-hgm1.onrender.com";
$config_file = __DIR__ . '/config.php';
if (file_exists($config_file)) {
    $rawCfg = @file_get_contents($config_file);
    if ($rawCfg && preg_match('/\$BACKEND_URL\s*=\s*[\'"]([^\'"]+)[\'"]/', $rawCfg, $m)) {
        $backend_url = $m[1];
    } elseif (include $config_file) {
        if (isset($BACKEND_URL) && !empty($BACKEND_URL)) {
            $backend_url = $BACKEND_URL;
        }
    }
}
$backend_url = rtrim($backend_url, '/');

// Handle CORS Preflight OPTIONS requests
if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    header("Access-Control-Allow-Origin: *");
    header("Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS, PATCH");
    header("Access-Control-Allow-Headers: Authorization, Content-Type, Accept, X-Requested-With");
    header("Access-Control-Max-Age: 86400");
    http_response_code(200);
    exit;
}

// Auto-Sync Webhook to dynamically update backend URL
if (isset($_GET['action']) && $_GET['action'] === 'sync_backend') {
    header("Content-Type: application/json");
    header("Access-Control-Allow-Origin: *");
    header("Access-Control-Allow-Headers: Authorization, Content-Type, Accept");

    $secret = $_POST['secret'] ?? $_GET['secret'] ?? '';
    $newUrl = $_POST['url'] ?? $_GET['url'] ?? '';

    // If input was raw JSON
    if (empty($secret) || empty($newUrl)) {
        $rawJson = @json_decode(file_get_contents('php://input'), true);
        if (is_array($rawJson)) {
            $secret = $secret ?: ($rawJson['secret'] ?? '');
            $newUrl = $newUrl ?: ($rawJson['url'] ?? '');
        }
    }

    $EXPECTED_SECRET = "sam_telegram_autopost_live_sync_2026_x99";

    if ($secret !== $EXPECTED_SECRET) {
        http_response_code(403);
        echo json_encode(["status" => "error", "message" => "Unauthorized sync request"]);
        exit;
    }

    $newUrl = trim($newUrl);
    if (empty($newUrl) || !filter_var($newUrl, FILTER_VALIDATE_URL)) {
        http_response_code(400);
        echo json_encode(["status" => "error", "message" => "Invalid backend URL provided"]);
        exit;
    }

    $newUrl = rtrim($newUrl, '/');
    $configContent = "<?php\n" .
                     "// ========================================================\n" .
                     "// Telegram AutoPost Pro - Hostinger Configuration\n" .
                     "// Auto-synced at: " . date('Y-m-d H:i:s T') . "\n" .
                     "// ========================================================\n\n" .
                     "\$BACKEND_URL = " . var_export($newUrl, true) . ";\n";

    $writeSuccess = @file_put_contents(__DIR__ . '/config.php', $configContent);
    if (function_exists('opcache_invalidate')) {
        @opcache_invalidate(__DIR__ . '/config.php', true);
    }
    if ($writeSuccess !== false) {
        echo json_encode([
            "status" => "success",
            "message" => "Backend URL updated successfully",
            "backend_url" => $newUrl,
            "updated_at" => date('Y-m-d H:i:s T')
        ]);
    } else {
        http_response_code(500);
        echo json_encode(["status" => "error", "message" => "Failed to write config.php on Hostinger"]);
    }
    exit;
}

// System Status & Health Check Action
if (isset($_GET['action']) && $_GET['action'] === 'status') {
    header("Content-Type: application/json");
    header("Access-Control-Allow-Origin: *");
    echo json_encode([
        "status" => "ok",
        "service" => "Telegram AutoPost Pro LiteSpeed Gateway",
        "configured_backend" => $backend_url,
        "config_writable" => is_writable(__DIR__ . '/config.php') || is_writable(__DIR__),
        "server_time" => date('Y-m-d H:i:s T')
    ]);
    exit;
}

// Persistent Cloud State Vault Actions (Guarantees zero account logout & persistence across Render restarts)
if (isset($_GET['action']) && $_GET['action'] === 'vault_push') {
    header("Content-Type: application/json");
    header("Access-Control-Allow-Origin: *");
    header("Access-Control-Allow-Headers: Authorization, Content-Type, Accept, X-Vault-Secret");

    $allHeaders = function_exists('getallheaders') ? getallheaders() : [];
    $secret = $_SERVER['HTTP_X_VAULT_SECRET'] 
        ?? $allHeaders['X-Vault-Secret'] 
        ?? $allHeaders['x-vault-secret'] 
        ?? $_POST['secret'] 
        ?? $_GET['secret'] 
        ?? '';
    $EXPECTED_SECRET = "sam_telegram_autopost_live_sync_2026_x99";

    if ($secret !== $EXPECTED_SECRET) {
        http_response_code(403);
        echo json_encode(["status" => "error", "message" => "Unauthorized vault push request"]);
        exit;
    }

    $rawInput = file_get_contents('php://input');
    $decodedCheck = json_decode($rawInput, true);
    if (empty($rawInput) || !is_array($decodedCheck) || !isset($decodedCheck['users'])) {
        http_response_code(400);
        echo json_encode(["status" => "error", "message" => "Invalid or incomplete vault payload"]);
        exit;
    }

    $vaultDir = __DIR__ . '/storage_vault';
    if (!is_dir($vaultDir)) {
        @mkdir($vaultDir, 0755, true);
        @file_put_contents($vaultDir . '/.htaccess', "<IfModule mod_authz_core.c>\n    Require all denied\n</IfModule>\n<IfModule !mod_authz_core.c>\n    Deny from all\n</IfModule>\n");
    }

    $tmpFile = $vaultDir . '/vault_state.json.tmp';
    $targetFile = $vaultDir . '/vault_state.json';
    $encryptedPayload = vault_encrypt($rawInput, $EXPECTED_SECRET);
    $saved = @file_put_contents($tmpFile, $encryptedPayload, LOCK_EX);
    if ($saved !== false && @rename($tmpFile, $targetFile)) {
        echo json_encode([
            "status" => "success",
            "message" => "Vault state successfully persisted & encrypted to Hostinger",
            "bytes" => $saved,
            "timestamp" => date('Y-m-d H:i:s T')
        ]);
    } else {
        @unlink($tmpFile);
        http_response_code(500);
        echo json_encode(["status" => "error", "message" => "Failed to write vault_state.json on Hostinger"]);
    }
    exit;
}

if (isset($_GET['action']) && $_GET['action'] === 'vault_pull') {
    header("Content-Type: application/json");
    header("Access-Control-Allow-Origin: *");
    header("Access-Control-Allow-Headers: Authorization, Content-Type, Accept, X-Vault-Secret");

    $allHeaders = function_exists('getallheaders') ? getallheaders() : [];
    $secret = $_SERVER['HTTP_X_VAULT_SECRET'] 
        ?? $allHeaders['X-Vault-Secret'] 
        ?? $allHeaders['x-vault-secret'] 
        ?? $_GET['secret'] 
        ?? '';
    $EXPECTED_SECRET = "sam_telegram_autopost_live_sync_2026_x99";

    if ($secret !== $EXPECTED_SECRET) {
        http_response_code(403);
        echo json_encode(["status" => "error", "message" => "Unauthorized vault pull request"]);
        exit;
    }

    $vaultFile = __DIR__ . '/storage_vault/vault_state.json';
    if (!file_exists($vaultFile)) {
        echo json_encode(["status" => "empty", "message" => "No vault state exists yet", "vault" => null]);
        exit;
    }

    $content = @file_get_contents($vaultFile);
    if ($content) {
        $decrypted = vault_decrypt($content, $EXPECTED_SECRET);
        echo $decrypted ?: $content;
    } else {
        echo json_encode(["status" => "empty", "message" => "Vault file empty", "vault" => null]);
    }
    exit;
}

// Direct Hostinger Mail Gateway action
if (isset($_GET['action']) && $_GET['action'] === 'send_mail') {
    header("Content-Type: application/json");
    header("Access-Control-Allow-Origin: *");
    header("Access-Control-Allow-Headers: Authorization, Content-Type, Accept");
    $rawInput = file_get_contents('php://input');
    $inputData = json_decode($rawInput, true);
    $to = $inputData['to'] ?? '';
    $subject = $inputData['subject'] ?? 'Telegram AutoPost Verification Code';
    $htmlBody = $inputData['html'] ?? '';

    if (empty($to) || empty($htmlBody)) {
        http_response_code(400);
        echo json_encode(["status" => "error", "message" => "Missing to or html parameters"]);
        exit;
    }

    $host = $_SERVER['HTTP_HOST'] ?? 'omeglevideochat.online';
    $fromEmail = 'noreply@' . preg_replace('/:\d+$/', '', $host);

    $headers = "MIME-Version: 1.0\r\n";
    $headers .= "Content-Type: text/html; charset=UTF-8\r\n";
    $headers .= "From: Telegram AutoPost Pro <{$fromEmail}>\r\n";
    $headers .= "Reply-To: support@{$fromEmail}\r\n";
    $headers .= "X-Mailer: PHP/" . phpversion();

    $sent = @mail($to, $subject, $htmlBody, $headers, "-f {$fromEmail}");
    if ($sent) {
        echo json_encode(["status" => "success", "message" => "Email dispatched via Hostinger mail gateway"]);
    } else {
        http_response_code(500);
        $lastErr = error_get_last();
        echo json_encode([
            "status" => "error", 
            "message" => "Hostinger PHP mail() failed to dispatch",
            "details" => $lastErr ? $lastErr['message'] : 'mail() returned false'
        ]);
    }
    exit;
}

// Determine target endpoint/route
$route = isset($_GET['route']) ? $_GET['route'] : '';
if (empty($route) && isset($_GET['endpoint'])) {
    $route = $_GET['endpoint'];
}
$route = ltrim($route, '/');

// Prepare query parameters without routing parameters
$queryParams = $_GET;
unset($queryParams['route'], $queryParams['endpoint']);
$queryString = http_build_query($queryParams);

// Build complete target backend URL
if (strpos($route, 'media/') === 0) {
    $targetUrl = $backend_url . '/' . $route;
} else {
    $targetUrl = $backend_url . '/api/' . $route;
}

if (!empty($queryString)) {
    $targetUrl .= (strpos($targetUrl, '?') === false ? '?' : '&') . $queryString;
}

// Extract Bearer Authorization header (LiteSpeed & Apache compatible)
$authHeader = '';
if (!empty($_SERVER['HTTP_AUTHORIZATION'])) {
    $authHeader = $_SERVER['HTTP_AUTHORIZATION'];
} elseif (!empty($_SERVER['REDIRECT_HTTP_AUTHORIZATION'])) {
    $authHeader = $_SERVER['REDIRECT_HTTP_AUTHORIZATION'];
} elseif (function_exists('apache_request_headers')) {
    $headers = apache_request_headers();
    foreach ($headers as $k => $v) {
        if (strcasecmp($k, 'Authorization') === 0) {
            $authHeader = $v;
            break;
        }
    }
}

// Prepare headers to forward
$headersToSend = [];
if (!empty($authHeader)) {
    $headersToSend[] = 'Authorization: ' . $authHeader;
}
// Do NOT forward Content-Type if uploading files; cURL must auto-generate its own multipart boundary
if (!empty($_SERVER['CONTENT_TYPE']) && empty($_FILES)) {
    $headersToSend[] = 'Content-Type: ' . $_SERVER['CONTENT_TYPE'];
}
$headersToSend[] = 'Accept: application/json, text/plain, */*';

// Initialize cURL session
$ch = curl_init($targetUrl);
$method = $_SERVER['REQUEST_METHOD'];

// Handle file uploads (multipart/form-data) or JSON bodies
if (!empty($_FILES)) {
    curl_setopt($ch, CURLOPT_POST, true);
    $postData = $_POST;
    foreach ($_FILES as $fileKey => $fileInfo) {
        if (is_uploaded_file($fileInfo['tmp_name'])) {
            $postData[$fileKey] = new CURLFile(
                $fileInfo['tmp_name'],
                $fileInfo['type'] ?: 'application/octet-stream',
                $fileInfo['name']
            );
        }
    }
    curl_setopt($ch, CURLOPT_POSTFIELDS, $postData);
} elseif (in_array($method, ['POST', 'PUT', 'PATCH', 'DELETE'])) {
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
    $body = file_get_contents('php://input');
    if (!empty($body)) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
    }
} else {
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
}

curl_setopt($ch, CURLOPT_HTTPHEADER, $headersToSend);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_HEADER, true);
curl_setopt($ch, CURLOPT_TIMEOUT, 90);
curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, false);
curl_setopt($ch, CURLOPT_FOLLOWLOCATION, true);

$response = curl_exec($ch);

if (curl_errno($ch)) {
    $curlErr = curl_error($ch);
    curl_close($ch);
    http_response_code(502);
    header("Content-Type: application/json");
    header("Access-Control-Allow-Origin: *");
    echo json_encode([
        "error" => "Backend Unavailable",
        "detail" => "The Telegram backend is temporarily offline or reconnecting. If running locally, please ensure the AutoPost service is running.",
        "message" => "Hostinger could not connect to the Telegram backend.",
        "backend_url" => $backend_url,
        "curl_error" => $curlErr
    ]);
    exit;
}

$httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$headerSize = curl_getinfo($ch, CURLINFO_HEADER_SIZE);
curl_close($ch);

$resHeaders = substr($response, 0, $headerSize);
$resBody = substr($response, $headerSize);

// ========================================================
// Persistent Data Vault Interceptor (Hostinger Permanent Storage)
// Guarantees accounts, groups, and posts never reset even if Render restarts
// ========================================================
$vaultDir = __DIR__ . '/storage_vault';

function _get_uid_from_auth_header($authH) {
    if (empty($authH)) return null;
    $tok = trim(str_ireplace('Bearer ', '', $authH));
    if (empty($tok)) return null;
    if (strpos($tok, 'v1.') === 0) {
        $parts = explode('.', $tok);
        if (count($parts) === 3) {
            $pad = str_repeat('=', (4 - strlen($parts[1]) % 4) % 4);
            $raw = base64_decode(strtr($parts[1], '-_', '+/') . $pad);
            $p = json_decode($raw, true);
            if (!empty($p['uid'])) return $p['uid'];
        }
    }
    $mapFile = __DIR__ . '/storage_vault/token_map.json';
    if (file_exists($mapFile)) {
        $map = @json_decode(@file_get_contents($mapFile), true);
        if (is_array($map) && !empty($map[$tok])) return $map[$tok];
    }
    return null;
}

if ($httpCode >= 200 && $httpCode < 300) {
    // 1. Capture user ID upon successful login
    if (strpos($route, 'auth/login') !== false) {
        $loginData = @json_decode($resBody, true);
        if (!empty($loginData['token']) && !empty($loginData['user']['id'])) {
            if (!is_dir($vaultDir)) { @mkdir($vaultDir, 0755, true); }
            $mapFile = $vaultDir . '/token_map.json';
            $map = file_exists($mapFile) ? (@json_decode(@file_get_contents($mapFile), true) ?: []) : [];
            $map[$loginData['token']] = $loginData['user']['id'];
            $map[$loginData['token'] . '_user'] = $loginData['user'];
            @file_put_contents($mapFile, json_encode($map, JSON_PRETTY_PRINT));
        }
    }

    $currentUid = _get_uid_from_auth_header($authHeader) ?: 'admin';
    $userVaultDir = $vaultDir . '/users/' . $currentUid;

    // 2. ACCOUNTS INTERCEPTOR
    if ($route === 'accounts' && $method === 'GET') {
        $accs = @json_decode($resBody, true);
        $accCacheFile = $userVaultDir . '/accounts.json';
        if (is_array($accs) && count($accs) > 0) {
            if (!is_dir($userVaultDir)) { @mkdir($userVaultDir, 0755, true); }
            @file_put_contents($accCacheFile, vault_encrypt($resBody, $EXPECTED_SECRET));
        } elseif ((!is_array($accs) || count($accs) === 0) && file_exists($accCacheFile)) {
            $cachedAccs = vault_decrypt(@file_get_contents($accCacheFile), $EXPECTED_SECRET);
            $decodedCached = @json_decode($cachedAccs, true);
            if (is_array($decodedCached) && count($decodedCached) > 0) {
                $resBody = $cachedAccs;
            }
        }
    } elseif (strpos($route, 'accounts/') === 0 && $method === 'DELETE') {
        $accCacheFile = $userVaultDir . '/accounts.json';
        $parts = explode('/', $route);
        $delId = end($parts);
        if (file_exists($accCacheFile) && !empty($delId)) {
            $existing = @json_decode(vault_decrypt(@file_get_contents($accCacheFile), $EXPECTED_SECRET), true);
            if (is_array($existing)) {
                $existing = array_values(array_filter($existing, function($a) use ($delId) {
                    return ($a['id'] ?? '') !== $delId;
                }));
                @file_put_contents($accCacheFile, vault_encrypt(json_encode($existing, JSON_PRETTY_PRINT), $EXPECTED_SECRET));
            }
        }
    }

    // 3. GROUPS INTERCEPTOR
    if ($route === 'groups' && $method === 'GET') {
        $grps = @json_decode($resBody, true);
        $grpCacheFile = $userVaultDir . '/groups.json';
        if (is_array($grps) && count($grps) > 0) {
            if (!is_dir($userVaultDir)) { @mkdir($userVaultDir, 0755, true); }
            @file_put_contents($grpCacheFile, vault_encrypt($resBody, $EXPECTED_SECRET));
        } elseif ((!is_array($grps) || count($grps) === 0) && file_exists($grpCacheFile)) {
            $cachedGrps = vault_decrypt(@file_get_contents($grpCacheFile), $EXPECTED_SECRET);
            $decodedCached = @json_decode($cachedGrps, true);
            if (is_array($decodedCached) && count($decodedCached) > 0) {
                $resBody = $cachedGrps;
            }
        }
    } elseif ($route === 'groups' && in_array($method, ['POST', 'PUT'])) {
        $grps = @json_decode($resBody, true);
        $grpCacheFile = $userVaultDir . '/groups.json';
        if (is_array($grps) && count($grps) > 0) {
            if (!is_dir($userVaultDir)) { @mkdir($userVaultDir, 0755, true); }
            @file_put_contents($grpCacheFile, vault_encrypt($resBody, $EXPECTED_SECRET));
        }
    } elseif ($route === 'groups' && $method === 'DELETE') {
        $grpCacheFile = $userVaultDir . '/groups.json';
        if (file_exists($grpCacheFile)) {
            @file_put_contents($grpCacheFile, vault_encrypt('[]', $EXPECTED_SECRET));
        }
    }

    // 4. POSTS INTERCEPTOR
    if ($route === 'posts' && $method === 'GET') {
        $posts = @json_decode($resBody, true);
        $postCacheFile = $userVaultDir . '/posts.json';
        $isDefault = is_array($posts) && count($posts) <= 3;
        if (is_array($posts) && count($posts) > 0 && !$isDefault) {
            if (!is_dir($userVaultDir)) { @mkdir($userVaultDir, 0755, true); }
            @file_put_contents($postCacheFile, vault_encrypt($resBody, $EXPECTED_SECRET));
        } elseif ($isDefault && file_exists($postCacheFile)) {
            $cachedPosts = vault_decrypt(@file_get_contents($postCacheFile), $EXPECTED_SECRET);
            $decodedCached = @json_decode($cachedPosts, true);
            if (is_array($decodedCached) && count($decodedCached) > count($posts)) {
                $resBody = $cachedPosts;
            }
        }
    } elseif (strpos($route, 'posts') === 0 && in_array($method, ['POST', 'PUT'])) {
        $pData = @json_decode($resBody, true);
        if (!empty($pData['id'])) {
            $postCacheFile = $userVaultDir . '/posts.json';
            $existing = file_exists($postCacheFile) ? (@json_decode(vault_decrypt(@file_get_contents($postCacheFile), $EXPECTED_SECRET), true) ?: []) : [];
            $found = false;
            foreach ($existing as &$ep) {
                if (($ep['id'] ?? '') === $pData['id']) {
                    $ep = array_merge($ep, $pData);
                    $found = true;
                    break;
                }
            }
            if (!$found) { $existing[] = $pData; }
            if (!is_dir($userVaultDir)) { @mkdir($userVaultDir, 0755, true); }
            @file_put_contents($postCacheFile, vault_encrypt(json_encode($existing, JSON_PRETTY_PRINT), $EXPECTED_SECRET));
        }
    } elseif (strpos($route, 'posts/') === 0 && $method === 'DELETE') {
        $postCacheFile = $userVaultDir . '/posts.json';
        $parts = explode('/', $route);
        $delId = end($parts);
        if (file_exists($postCacheFile) && !empty($delId)) {
            $existing = @json_decode(vault_decrypt(@file_get_contents($postCacheFile), $EXPECTED_SECRET), true);
            if (is_array($existing)) {
                $existing = array_values(array_filter($existing, function($p) use ($delId) {
                    return ($p['id'] ?? '') !== $delId;
                }));
                @file_put_contents($postCacheFile, vault_encrypt(json_encode($existing, JSON_PRETTY_PRINT), $EXPECTED_SECRET));
            }
        }
    }

    // 5. STATUS ENRICHMENT
    if ($route === 'status' && $method === 'GET') {
        $stData = @json_decode($resBody, true);
        if (is_array($stData)) {
            $accCache = $userVaultDir . '/accounts.json';
            $grpCache = $userVaultDir . '/groups.json';
            $postCache = $userVaultDir . '/posts.json';
            if (isset($stData['total_accounts']) && $stData['total_accounts'] === 0 && file_exists($accCache)) {
                $sAccs = @json_decode(vault_decrypt(@file_get_contents($accCache), $EXPECTED_SECRET), true);
                if (is_array($sAccs) && count($sAccs) > 0) {
                    $stData['total_accounts'] = count($sAccs);
                    $stData['active_accounts'] = count(array_filter($sAccs, function($a) { return ($a['is_active'] ?? true) !== false; }));
                }
            }
            if (isset($stData['total_groups']) && $stData['total_groups'] === 0 && file_exists($grpCache)) {
                $sGrps = @json_decode(vault_decrypt(@file_get_contents($grpCache), $EXPECTED_SECRET), true);
                if (is_array($sGrps) && count($sGrps) > 0) {
                    $stData['total_groups'] = count($sGrps);
                }
            }
            if (file_exists($postCache)) {
                $sPosts = @json_decode(vault_decrypt(@file_get_contents($postCache), $EXPECTED_SECRET), true);
                if (is_array($sPosts) && count($sPosts) > 3) {
                    $stData['total_posts'] = count($sPosts);
                    $stData['active_posts'] = count(array_filter($sPosts, function($p) { return ($p['is_active'] ?? true) !== false; }));
                }
            }
            $resBody = json_encode($stData);
        }
    }
}

// Send CORS and Status
header("Access-Control-Allow-Origin: *");
header("Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS, PATCH");
header("Access-Control-Allow-Headers: Authorization, Content-Type, Accept, X-Requested-With");
http_response_code($httpCode);

// Forward Content-Type header from backend
$headerLines = explode("\r\n", $resHeaders);
foreach ($headerLines as $line) {
    if (stripos($line, 'Content-Type:') === 0) {
        header($line);
    }
}

echo $resBody;
