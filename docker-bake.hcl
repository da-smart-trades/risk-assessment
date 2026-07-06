
group "default" {
    targets = ["ingester", "dashboard", "plugins"]
}


variable "DOCKER_VERSION" {
    default = "0.0.0"
    validation {
        condition = DOCKER_VERSION != ""
        error_message = "No `DOCKER_VERSION`."
    }
}

variable "WHEEL_NAME" {
    default = null
    validation {
        condition = WHEEL_NAME != null && WHEEL_NAME != ""
        error_message = "No wheel."
    }
}

variable USE_CACHE {
    default = 0
}

variable "CACHE_FROM" {
    default = "registry"
}

variable ADD_LATEST {
    default = 0
}

variable "IMAGE_BASE_NAME" {
    # Local image name by default. Override (e.g. export IMAGE_BASE_NAME=
    # <acct>.dkr.ecr.<region>.amazonaws.com/<repo>) to push to your own registry.
    default = "risk-assessment"
}

target "common" {
    platforms = ["linux/arm64", "linux/amd64"]
    args = {
        WHEEL_NAME = "${WHEEL_NAME}"
    }


}

target "ingester" {
    inherits = ["common"]

    target = "ingester"

    cache-from = [
        USE_CACHE > 0 ?
            CACHE_FROM == "registry" ?  "type=${CACHE_FROM},ref=${IMAGE_BASE_NAME}:buildcache-ingester" : "type=${CACHE_FROM}"
        : "",
    ]
    cache-to = [
        USE_CACHE > 0 ?
            CACHE_FROM == "registry" ? "type=${CACHE_FROM},ref=${IMAGE_BASE_NAME}:buildcache-ingester,image-manifest=true,mode=max,oci-mediatypes=true,compression=zstd" : "type=${CACHE_FROM},mode=max"
        : "",
    ]
    tags = [
       "${IMAGE_BASE_NAME}:${DOCKER_VERSION}-ingester",
       ADD_LATEST > 0 ? "${IMAGE_BASE_NAME}:latest-ingester" : "",

    ]

}

target "dashboard" {
    inherits = ["common"]

    target = "dashboard"

    cache-from = [
        USE_CACHE > 0 ?
            CACHE_FROM == "registry" ?  "type=${CACHE_FROM},ref=${IMAGE_BASE_NAME}:buildcache-dashboard" : "type=${CACHE_FROM}"
        : "",
    ]
    cache-to = [
        USE_CACHE > 0 ?
            CACHE_FROM == "registry" ? "type=${CACHE_FROM},ref=${IMAGE_BASE_NAME}:buildcache-dashboard,image-manifest=true,mode=max,oci-mediatypes=true,compression=zstd" : "type=${CACHE_FROM},mode=max"
        : "",
    ]
    tags = [
       "${IMAGE_BASE_NAME}:${DOCKER_VERSION}-dashboard",
       ADD_LATEST > 0 ? "${IMAGE_BASE_NAME}:latest-dashboard" : "",

    ]
}


target "plugins" {
    inherits = ["common"]

    target = "plugins"

    cache-from = [
        USE_CACHE > 0 ?
            CACHE_FROM == "registry" ?  "type=${CACHE_FROM},ref=${IMAGE_BASE_NAME}:buildcache-plugins" : "type=${CACHE_FROM}"
        : "",
    ]
    cache-to = [
        USE_CACHE > 0 ?
            CACHE_FROM == "registry" ? "type=${CACHE_FROM},ref=${IMAGE_BASE_NAME}:buildcache-plugins,image-manifest=true,mode=max,oci-mediatypes=true,compression=zstd" : "type=${CACHE_FROM},mode=max"
        : "",
    ]
    tags = [
       "${IMAGE_BASE_NAME}:${DOCKER_VERSION}-plugins",
       ADD_LATEST > 0 ? "${IMAGE_BASE_NAME}:latest-plugins" : "",

    ]
}
