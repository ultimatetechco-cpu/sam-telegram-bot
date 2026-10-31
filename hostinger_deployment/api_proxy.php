<?php
// ========================================================
// Telegram AutoPost Pro - Hostinger LiteSpeed API Gateway
// Proxies all frontend API & media calls to the active Python backend
// ========================================================

// Suppress raw error reporting to keep JSON responses clean
error_reporting(0);
@ini_set('display_errors', '0');

// Load backend target URL from config.php
$backend_url = "https://kate-tramadol-postal-supporting.trycloudflare.com";
$config_file = __DIR__ . '/config.php';
if (file_exists($config_file)) {
    include $config_file;
    if (isset($BACKEND_URL) && !empty($BACKEND_URL)) {
        $backend_url = $BACKEND_URL;
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
if (!empty($_SERVER['CONTENT_TYPE'])) {
    $headersToSend[] = 'Content-Type: ' . $_SERVER['CONTENT_TYPE'];
}
$headersToSend[] = 'Accept: application/json, text/plain, */*';

// Initialize cURL session
$ch = curl_init($targetUrl);
$method = $_SERVER['REQUEST_METHOD'];
curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);

// Handle file uploads (multipart/form-data) or JSON bodies
if (!empty($_FILES)) {
    $postData = $_POST;
    foreach ($_FILES as $fileKey => $fileInfo) {
        if (is_uploaded_file($fileInfo['tmp_name'])) {
            $postData[$fileKey] = new CURLFile(
                $fileInfo['tmp_name'],
                $fileInfo['type'],
                $fileInfo['name']
            );
        }
    }
    curl_setopt($ch, CURLOPT_POSTFIELDS, $postData);
} elseif (in_array($method, ['POST', 'PUT', 'PATCH', 'DELETE'])) {
    $body = file_get_contents('php://input');
    if (!empty($body)) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
    }
}

curl_setopt($ch, CURLOPT_HTTPHEADER, $headersToSend);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_HEADER, true);
curl_setopt($ch, CURLOPT_TIMEOUT, 60);
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
