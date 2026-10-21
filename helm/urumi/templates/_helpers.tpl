{{- define "urumi.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "urumi.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "urumi.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "urumi.labels" -}}
helm.sh/chart: {{ include "urumi.chart" . }}
app.kubernetes.io/name: {{ include "urumi.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: urumi
{{- end }}

{{- define "urumi.selectorLabels" -}}
{{- $root := index . 0 }}
{{- $component := index . 1 }}
app.kubernetes.io/name: {{ include "urumi.name" $root }}
app.kubernetes.io/instance: {{ $root.Release.Name }}
app.kubernetes.io/component: {{ $component }}
{{- end }}

{{- define "urumi.componentLabels" -}}
{{- $root := index . 0 }}
{{- $component := index . 1 }}
{{ include "urumi.labels" $root }}
app.kubernetes.io/component: {{ $component }}
{{- end }}

{{- define "urumi.serviceName" -}}
{{- $name := printf "%s-%s" (include "urumi.fullname" (index . 0)) (index . 1) }}
{{- if gt (len $name) 63 }}
{{- fail (printf "Service name %s is longer than 63 characters." $name) }}
{{- end }}
{{- $name }}
{{- end }}

{{- define "urumi.probeFrom" -}}
{{- range .Values.networkPolicy.probeCIDRs }}
- ipBlock:
    cidr: {{ . | quote }}
{{- end }}
{{- end }}

{{- define "urumi.databaseHost" -}}
{{- include "urumi.serviceName" (list . "gateway-db") }}
{{- end }}

{{- define "urumi.serviceAccountName" -}}
{{- include "urumi.serviceName" (list . "runtime") }}
{{- end }}

{{- define "urumi.configName" -}}
{{- include "urumi.serviceName" (list . "config") }}
{{- end }}

{{- define "urumi.databaseSecretName" -}}
{{- if .Values.database.existingSecret }}
{{- .Values.database.existingSecret }}
{{- else }}
{{- include "urumi.serviceName" (list . "gateway-db") }}
{{- end }}
{{- end }}

{{- define "urumi.storeSecretName" -}}
{{- if .Values.store.existingSecret }}
{{- .Values.store.existingSecret }}
{{- else }}
{{- include "urumi.serviceName" (list . "store") }}
{{- end }}
{{- end }}

{{- define "urumi.publicStoreUrl" -}}
{{- trimSuffix "/" .Values.store.publicUrl }}
{{- end }}

{{- define "urumi.wooBaseUrl" -}}
{{- if .Values.woocommerce.baseUrl -}}
{{- trimSuffix "/" .Values.woocommerce.baseUrl -}}
{{- else if .Values.store.enabled -}}
{{- printf "http://%s" (include "urumi.serviceName" (list . "wordpress")) -}}
{{- end -}}
{{- end }}

{{- define "urumi.wooSiteUrl" -}}
{{- if .Values.woocommerce.siteUrl -}}
{{- trimSuffix "/" .Values.woocommerce.siteUrl -}}
{{- else if .Values.store.enabled -}}
{{- include "urumi.publicStoreUrl" . -}}
{{- end -}}
{{- end }}

{{- define "urumi.image" -}}
{{- printf "%s:%s" .repository .tag }}
{{- end }}

{{- define "urumi.libpqValue" -}}
{{- . | replace `\` `\\` | replace `'` `\'` }}
{{- end }}

{{- define "urumi.databaseConninfo" -}}
{{- $host := include "urumi.libpqValue" (include "urumi.databaseHost" .) }}
{{- $name := include "urumi.libpqValue" .Values.database.name }}
{{- $user := include "urumi.libpqValue" .Values.database.user }}
{{- $password := include "urumi.libpqValue" .Values.database.password }}
host='{{ $host }}' port={{ .Values.database.port }} dbname='{{ $name }}' user='{{ $user }}' password='{{ $password }}'
{{- end }}

{{- define "urumi.resources" -}}
{{- with . }}
resources:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{- define "urumi.httpProbe" -}}
httpGet:
  path: {{ .path }}
  port: http
initialDelaySeconds: {{ .initialDelaySeconds }}
periodSeconds: {{ .periodSeconds }}
timeoutSeconds: {{ .timeoutSeconds }}
failureThreshold: {{ .failureThreshold }}
{{- end }}

{{- define "urumi.tcpProbe" -}}
tcpSocket:
  port: http
initialDelaySeconds: {{ .initialDelaySeconds }}
periodSeconds: {{ .periodSeconds }}
timeoutSeconds: {{ .timeoutSeconds }}
failureThreshold: {{ .failureThreshold }}
{{- end }}

{{- define "urumi.mariadbProbe" -}}
{{- $probe := index . 1 }}
exec:
  command:
    - healthcheck.sh
    - --connect
    - --innodb_initialized
initialDelaySeconds: {{ $probe.initialDelaySeconds }}
periodSeconds: {{ $probe.periodSeconds }}
timeoutSeconds: {{ $probe.timeoutSeconds }}
failureThreshold: {{ $probe.failureThreshold }}
{{- end }}

{{- define "urumi.postgresProbe" -}}
{{- $root := index . 0 }}
{{- $probe := index . 1 }}
exec:
  command:
    - pg_isready
    - -U
    - {{ $root.Values.database.user | quote }}
    - -d
    - {{ $root.Values.database.name | quote }}
initialDelaySeconds: {{ $probe.initialDelaySeconds }}
periodSeconds: {{ $probe.periodSeconds }}
timeoutSeconds: {{ $probe.timeoutSeconds }}
failureThreshold: {{ $probe.failureThreshold }}
{{- end }}

{{- define "urumi.validate" -}}
{{- if and .Values.database.existingSecret .Values.database.password }}
{{- fail "Set database.existingSecret or database.password, not both." }}
{{- end }}
{{- if and (not .Values.database.existingSecret) (not .Values.database.password) }}
{{- fail "Set database.existingSecret, or set database.password in a gitignored values file." }}
{{- end }}
{{- if not .Values.database.existingSecret }}
{{- if or (contains "\n" .Values.database.password) (contains "\r" .Values.database.password) }}
{{- fail "database.password must be a single line." }}
{{- end }}
{{- end }}
{{- if or (not .Values.database.secretKeys.password) (not .Values.database.secretKeys.conninfo) }}
{{- fail "database.secretKeys.password and database.secretKeys.conninfo are required." }}
{{- end }}
{{- if ne (int .Values.database.replicas) 1 }}
{{- fail "This chart runs one bundled PostgreSQL replica." }}
{{- end }}
{{- if or (not .Values.database.user) (not .Values.database.name) }}
{{- fail "database.user and database.name are required." }}
{{- end }}
{{- if .Values.store.enabled }}
{{- if and .Values.store.existingSecret (or .Values.store.mariadb.password .Values.store.mariadb.rootPassword .Values.store.wordpress.adminPassword) }}
{{- fail "Set store.existingSecret or the store passwords, not both." }}
{{- end }}
{{- if not .Values.store.existingSecret }}
{{- if or (not .Values.store.mariadb.password) (not .Values.store.mariadb.rootPassword) (not .Values.store.wordpress.adminPassword) (not .Values.store.wordpress.adminUser) (not .Values.store.wordpress.adminEmail) }}
{{- fail "Set store.existingSecret, or set the MariaDB and WordPress admin values in a gitignored values file." }}
{{- end }}
{{- if or (contains "\n" .Values.store.mariadb.password) (contains "\r" .Values.store.mariadb.password) (contains "\n" .Values.store.mariadb.rootPassword) (contains "\r" .Values.store.mariadb.rootPassword) (contains "\n" .Values.store.wordpress.adminPassword) (contains "\r" .Values.store.wordpress.adminPassword) }}
{{- fail "Store passwords must be a single line." }}
{{- end }}
{{- end }}
{{- if not .Values.store.publicUrl }}
{{- fail "store.publicUrl is required when the store is enabled. Use the public WordPress address." }}
{{- end }}
{{- $storeUrl := urlParse (include "urumi.publicStoreUrl" .) }}
{{- if or (not $storeUrl.scheme) (not $storeUrl.host) }}
{{- fail "store.publicUrl must be an absolute http or https URL." }}
{{- end }}
{{- if and (ne $storeUrl.scheme "http") (ne $storeUrl.scheme "https") }}
{{- fail "store.publicUrl must be an absolute http or https URL." }}
{{- end }}
{{- if $storeUrl.userinfo }}
{{- fail "store.publicUrl must not include credentials." }}
{{- end }}
{{- if or (not .Values.store.databaseName) (not .Values.store.databaseUser) }}
{{- fail "store.databaseName and store.databaseUser are required." }}
{{- end }}
{{- if ne (int .Values.store.mariadb.replicas) 1 }}
{{- fail "This chart runs one MariaDB replica." }}
{{- end }}
{{- if ne (int .Values.store.wordpress.replicas) 1 }}
{{- fail "This chart runs one WordPress replica. The content volume is ReadWriteOnce." }}
{{- end }}
{{- if and .Values.ingress.enabled .Values.ingress.store.enabled }}
{{- if ne $storeUrl.host .Values.ingress.store.host }}
{{- fail "ingress.store.host must match the host in store.publicUrl." }}
{{- end }}
{{- end }}
{{- end }}
{{- if .Values.ingress.enabled }}
{{- if and (not .Values.ingress.store.enabled) (not .Values.ingress.gateway.enabled) (not .Values.ingress.dashboard.enabled) }}
{{- fail "ingress.enabled requires a store, gateway, or dashboard route." }}
{{- end }}
{{- if and .Values.ingress.store.enabled (not .Values.store.enabled) }}
{{- fail "ingress.store.enabled requires store.enabled." }}
{{- end }}
{{- if and .Values.ingress.dashboard.enabled (not .Values.dashboard.enabled) }}
{{- fail "ingress.dashboard.enabled requires dashboard.enabled." }}
{{- end }}
{{- if and .Values.ingress.store.enabled (not .Values.ingress.store.host) }}
{{- fail "ingress.store.host is required when the store route is enabled." }}
{{- end }}
{{- if and .Values.ingress.gateway.enabled (not .Values.ingress.gateway.host) }}
{{- fail "ingress.gateway.host is required when the gateway route is enabled." }}
{{- end }}
{{- if and .Values.ingress.dashboard.enabled (not .Values.ingress.dashboard.host) }}
{{- fail "ingress.dashboard.host is required when the dashboard route is enabled." }}
{{- end }}
{{- if and .Values.gateway.cookieSecure (not .Values.ingress.tls) }}
{{- fail "gateway.cookieSecure requires ingress.tls. The session cookie is marked Secure." }}
{{- end }}
{{- end }}
{{- if and .Values.store.enabled .Values.store.demoPayment (ne .Values.store.demoPayment "cod") }}
{{- fail "store.demoPayment must be empty or cod." }}
{{- end }}
{{- if .Values.dashboard.enabled }}
{{- if or (not .Values.dashboard.image.repository) (not .Values.dashboard.image.tag) }}
{{- fail "dashboard.image needs a repository and a tag." }}
{{- end }}
{{- end }}
{{- $images := list .Values.gateway.image .Values.database.image .Values.upstreams.support.image .Values.upstreams.utility.image .Values.upstreams.woocommerce.image }}
{{- if .Values.store.enabled }}
{{- $images = append $images .Values.store.mariadb.image }}
{{- $images = append $images .Values.store.wordpress.image }}
{{- $images = append $images .Values.store.init.image }}
{{- end }}
{{- range $images }}
{{- if or (not .repository) (not .tag) }}
{{- fail "Every image needs a repository and a tag." }}
{{- end }}
{{- end }}
{{- if and .Values.storeProvisioner.enabled (not .Values.storeProvisioner.admit) }}
{{- fail "The store provisioner is non-deployable until storeProvisioner.admit is true and the admission policy is installed. Do not enable it on the live release." }}
{{- end }}
{{- end }}
