package com.aih.preview

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import coil.compose.AsyncImage
import coil.ImageLoader
import coil.request.ImageRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import java.util.concurrent.TimeUnit

// ─── Data Models ────────────────────────────────────────────────────────────

data class PreviewImage(
    val id: String,
    val timestamp: Long
)

data class SettingsData(
    val baseUrl: String = "",
    val apiKey: String = ""
)

enum class ScreenState {
    SETTINGS,
    LOADING,
    GALLERY,
    ERROR
}

// ─── ViewModel ───────────────────────────────────────────────────────────────

class PreviewViewModel : ViewModel() {

    private val prefsName = "aih_prefs"

    private val _screenState = MutableStateFlow(ScreenState.SETTINGS)
    val screenState: StateFlow<ScreenState> = _screenState.asStateFlow()

    private val _settings = MutableStateFlow(SettingsData())
    val settings: StateFlow<SettingsData> = _settings.asStateFlow()

    private val _images = MutableStateFlow<List<PreviewImage>>(emptyList())
    val images: StateFlow<List<PreviewImage>> = _images.asStateFlow()

    private val _loading = MutableStateFlow(false)
    val loading: StateFlow<Boolean> = _loading.asStateFlow()

    private val _errorMsg = MutableStateFlow<String?>(null)
    val errorMsg: StateFlow<String?> = _errorMsg.asStateFlow()

    var okhttpClient: OkHttpClient? = null
        private set

    var imageLoader: ImageLoader? = null
        private set

    fun loadSettings(context: android.content.Context) {
        val prefs = context.getSharedPreferences(prefsName, android.content.Context.MODE_PRIVATE)
        val url = prefs.getString("base_url", "") ?: ""
        val key = prefs.getString("api_key", "") ?: ""
        _settings.value = SettingsData(url, key)
        if (url.isNotEmpty() && key.isNotEmpty()) {
            initClients(url, key)
            testConnection()
        } else {
            _screenState.value = ScreenState.SETTINGS
        }
    }

    fun saveSettingsAndConnect(context: android.content.Context, url: String, key: String) {
        val prefs = context.getSharedPreferences(prefsName, android.content.Context.MODE_PRIVATE)
        prefs.edit().apply {
            putString("base_url", url.trimEnd('/'))
            putString("api_key", key)
            apply()
        }
        _settings.value = SettingsData(url.trimEnd('/'), key)
        initClients(url.trimEnd('/'), key)
        testConnection()
    }

    private fun initClients(baseUrl: String, apiKey: String) {
        val client = OkHttpClient.Builder()
            .addInterceptor { chain ->
                val original = chain.request()
                val request = original.newBuilder()
                    .header("Authorization", "Bearer $apiKey")
                    .build()
                chain.proceed(request)
            }
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .build()
        okhttpClient = client
        // imageLoader will be created in setImageLoaderContext once we have a Context
    }

    fun setImageLoaderContext(context: android.content.Context) {
        val client = okhttpClient ?: return
        imageLoader = ImageLoader.Builder(context)
            .okHttpClient(client)
            .build()
    }

    fun testConnection() {
        val url = _settings.value.baseUrl
        if (url.isEmpty()) {
            _screenState.value = ScreenState.SETTINGS
            return
        }
        _screenState.value = ScreenState.LOADING
        _errorMsg.value = null
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val client = OkHttpClient.Builder()
                    .addInterceptor { chain ->
                        val original = chain.request()
                        val request = original.newBuilder()
                            .header("Authorization", "Bearer ${_settings.value.apiKey}")
                            .build()
                        chain.proceed(request)
                    }
                    .build()
                val request = Request.Builder()
                    .url("$url/api/preview/recent")
                    .get()
                    .build()
                client.newCall(request).execute().use { response ->
                    if (response.isSuccessful) {
                        val body = response.body?.string()
                        val images = parseImages(body)
                        _images.value = images
                        _screenState.value = ScreenState.GALLERY
                    } else {
                        _errorMsg.value = "Erreur ${response.code}: ${response.message}"
                        _screenState.value = ScreenState.ERROR
                    }
                }
            } catch (e: Exception) {
                _errorMsg.value = "Connexion échouée: ${e.message}"
                _screenState.value = ScreenState.ERROR
            }
        }
    }

    fun fetchImages() {
        val url = _settings.value.baseUrl
        val apiKey = _settings.value.apiKey
        if (url.isEmpty()) return
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val client = okhttpClient ?: return@launch
                val request = Request.Builder()
                    .url("$url/api/preview/recent")
                    .get()
                    .build()
                client.newCall(request).execute().use { response ->
                    if (response.isSuccessful) {
                        val body = response.body?.string()
                        val images = parseImages(body)
                        _images.value = images
                    }
                }
            } catch (e: Exception) {
                // silent fail during polling
            }
        }
    }

    fun goToSettings() {
        _screenState.value = ScreenState.SETTINGS
    }

    fun clearError() {
        _errorMsg.value = null
    }

    private fun parseImages(body: String?): List<PreviewImage> {
        if (body.isNullOrBlank()) return emptyList()
        val result = mutableListOf<PreviewImage>()
        try {
            val arr = JSONArray(body)
            for (i in 0 until arr.length()) {
                val obj = arr.getJSONObject(i)
                val id = obj.optString("id", obj.optString("_id", ""))
                val ts = obj.optLong("timestamp", obj.optLong("createdAt", 0L))
                if (id.isNotEmpty()) {
                    result.add(PreviewImage(id, ts))
                }
            }
        } catch (e: Exception) {
            // try single object
            try {
                val obj = org.json.JSONObject(body)
                val id = obj.optString("id", obj.optString("_id", ""))
                if (id.isNotEmpty()) {
                    result.add(PreviewImage(id, obj.optLong("timestamp", 0L)))
                }
            } catch (_: Exception) {
            }
        }
        return result.sortedByDescending { it.timestamp }
    }
}

// ─── MainActivity ────────────────────────────────────────────────────────────

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(
                colorScheme = darkColorScheme()
            ) {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    AppContent()
                }
            }
        }
    }
}

@Composable
fun AppContent() {
    val context = LocalContext.current
    val viewModel = remember { PreviewViewModel() }
    val screenState by viewModel.screenState.collectAsState()
    val images by viewModel.images.collectAsState()
    val settings by viewModel.settings.collectAsState()
    val loading by viewModel.loading.collectAsState()
    val errorMsg by viewModel.errorMsg.collectAsState()

    // Initialize on first composition
    LaunchedEffect(Unit) {
        viewModel.loadSettings(context)
        viewModel.setImageLoaderContext(context)
    }

    when (screenState) {
        ScreenState.SETTINGS -> SettingsScreen(
            initialUrl = settings.baseUrl,
            initialKey = settings.apiKey,
            onConnect = { url, key ->
                viewModel.saveSettingsAndConnect(context, url, key)
                viewModel.setImageLoaderContext(context)
            }
        )

        ScreenState.LOADING -> {
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center
            ) {
                CircularProgressIndicator()
            }
        }

        ScreenState.GALLERY -> GalleryScreen(
            viewModel = viewModel,
            images = images,
            baseUrl = settings.baseUrl
        )

        ScreenState.ERROR -> {
            Column(
                modifier = Modifier.fillMaxSize().padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center
            ) {
                Text(
                    text = errorMsg ?: "Erreur inconnue",
                    color = MaterialTheme.colorScheme.error,
                    fontSize = 16.sp
                )
                Spacer(modifier = Modifier.height(16.dp))
                Button(onClick = { viewModel.goToSettings() }) {
                    Text("Retour aux paramètres")
                }
                Spacer(modifier = Modifier.height(8.dp))
                Button(onClick = { viewModel.testConnection() }) {
                    Text("Réessayer")
                }
            }
        }
    }
}

// ─── Settings Screen ─────────────────────────────────────────────────────────

@Composable
fun SettingsScreen(
    initialUrl: String,
    initialKey: String,
    onConnect: (String, String) -> Unit
) {
    var url by remember { mutableStateOf(TextFieldValue(initialUrl)) }
    var key by remember { mutableStateOf(TextFieldValue(initialKey)) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text(
            text = "AIH Preview",
            style = MaterialTheme.typography.headlineMedium,
            color = MaterialTheme.colorScheme.primary
        )
        Spacer(modifier = Modifier.height(32.dp))

        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("URL Backend") },
            placeholder = { Text("http://192.168.1.100:3000") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth()
        )
        Spacer(modifier = Modifier.height(16.dp))

        OutlinedTextField(
            value = key,
            onValueChange = { key = it },
            label = { Text("API Key") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth()
        )
        Spacer(modifier = Modifier.height(24.dp))

        Button(
            onClick = { onConnect(url.text, key.text) },
            modifier = Modifier.fillMaxWidth(),
            enabled = url.text.isNotBlank() && key.text.isNotBlank()
        ) {
            Text("Connecter")
        }
    }
}

// ─── Gallery Screen ──────────────────────────────────────────────────────────

@Composable
fun GalleryScreen(
    viewModel: PreviewViewModel,
    images: List<PreviewImage>,
    baseUrl: String
) {
    val imageLoader = viewModel.imageLoader
    val context = LocalContext.current

    // Polling: fetch every 5 seconds
    LaunchedEffect(baseUrl) {
        while (true) {
            delay(5000)
            viewModel.fetchImages()
        }
    }

    Scaffold(
        topBar = {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "AIH Preview",
                    style = MaterialTheme.typography.titleLarge,
                    color = MaterialTheme.colorScheme.primary
                )
                Row(
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "${images.size} image(s)",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(modifier = Modifier.width(12.dp))
                    IconButton(onClick = { viewModel.goToSettings() }) {
                        Icon(
                            imageVector = Icons.Default.Settings,
                            contentDescription = "Settings",
                            tint = MaterialTheme.colorScheme.onSurface
                        )
                    }
                }
            }
        }
    ) { paddingValues ->
        if (images.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(paddingValues),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = "Aucune image pour l'instant",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        } else {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(paddingValues)
                    .padding(horizontal = 8.dp)
            ) {
                // Most recent image – large
                val mostRecent = images.first()
                AuthAsyncImage(
                    url = "$baseUrl/api/preview/image/${mostRecent.id}",
                    imageLoader = imageLoader,
                    contentDescription = "Most recent",
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(280.dp)
                        .padding(4.dp),
                    contentScale = ContentScale.Crop
                )

                Spacer(modifier = Modifier.height(8.dp))

                // Rest in grid
                val rest = images.drop(1)
                if (rest.isNotEmpty()) {
                    LazyVerticalGrid(
                        columns = GridCells.Fixed(2),
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(4.dp),
                        horizontalArrangement = Arrangement.spacedBy(4.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp)
                    ) {
                        items(rest) { img ->
                            AuthAsyncImage(
                                url = "$baseUrl/api/preview/image/${img.id}",
                                imageLoader = imageLoader,
                                contentDescription = "Preview ${img.id}",
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .aspectRatio(1f),
                                contentScale = ContentScale.Crop
                            )
                        }
                    }
                }
            }
        }
    }
}

// ─── Auth AsyncImage (Coil with custom OkHttp) ───────────────────────────────

@Composable
fun AuthAsyncImage(
    url: String,
    imageLoader: ImageLoader?,
    contentDescription: String?,
    modifier: Modifier = Modifier,
    contentScale: ContentScale = ContentScale.Crop
) {
    val context = LocalContext.current
    val request = remember(url) {
        ImageRequest.Builder(context)
            .data(url)
            .crossfade(true)
            .build()
    }

    if (imageLoader != null) {
        AsyncImage(
            model = request,
            contentDescription = contentDescription,
            imageLoader = imageLoader,
            contentScale = contentScale,
            modifier = modifier
                .clip(RoundedCornerShape(8.dp))
                .background(Color(0xFF1E1E1E))
        )
    } else {
        Box(
            modifier = modifier
                .clip(RoundedCornerShape(8.dp))
                .background(Color(0xFF1E1E1E)),
            contentAlignment = Alignment.Center
        ) {
            CircularProgressIndicator(modifier = Modifier.size(24.dp))
        }
    }
}

