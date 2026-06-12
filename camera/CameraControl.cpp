#include <chrono>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>

#include "EDSDK.h"
#include "EDSDKErrors.h"
#include "EDSDKTypes.h"

namespace {

std::string errorToHex(EdsError err) {
    std::ostringstream stream;
    stream << "0x" << std::hex << std::uppercase << err;
    return stream.str();
}

long long epochMicroseconds() {
    return std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();
}

void printError(const std::string& action, EdsError err) {
    std::cerr << "ERR " << action << " failed: " << errorToHex(err) << std::endl;
}

class CameraControl {
public:
    ~CameraControl() {
        shutdown();
    }

    EdsError initialize() {
        if (sessionOpen_) {
            return EDS_ERR_OK;
        }

        EdsError err = EDS_ERR_OK;

        if (!sdkInitialized_) {
            err = EdsInitializeSDK();
            if (err != EDS_ERR_OK) {
                printError("EdsInitializeSDK", err);
                return err;
            }
            sdkInitialized_ = true;
        }

        EdsCameraListRef cameraList = nullptr;
        EdsUInt32 count = 0;

        err = EdsGetCameraList(&cameraList);
        if (err == EDS_ERR_OK) {
            err = EdsGetChildCount(cameraList, &count);
        }

        if (err == EDS_ERR_OK && count == 0) {
            err = EDS_ERR_DEVICE_NOT_FOUND;
        }

        if (err == EDS_ERR_OK) {
            err = EdsGetChildAtIndex(cameraList, 0, &camera_);
        }

        if (cameraList != nullptr) {
            EdsRelease(cameraList);
        }

        if (err != EDS_ERR_OK) {
            printError("camera discovery", err);
            return err;
        }

        err = EdsOpenSession(camera_);
        if (err != EDS_ERR_OK) {
            printError("EdsOpenSession", err);
            return err;
        }
        sessionOpen_ = true;

        EdsUInt32 saveTo = kEdsSaveTo_Camera;
        err = EdsSetPropertyData(camera_, kEdsPropID_SaveTo, 0, sizeof(saveTo), &saveTo);
        if (err != EDS_ERR_OK) {
            printError("set SaveTo", err);
            return err;
        }

        EdsCapacity capacity = {0x7FFFFFFF, 0x1000, 1};
        EdsSetCapacity(camera_, capacity);

        enableMovieMode();
        return EDS_ERR_OK;
    }

    EdsError startRecording() {
        EdsError err = initialize();
        if (err != EDS_ERR_OK) {
            return err;
        }

        EdsUInt32 saveTo = kEdsSaveTo_Camera;
        EdsSetPropertyData(camera_, kEdsPropID_SaveTo, 0, sizeof(saveTo), &saveTo);

        EdsUInt32 recordStart = 4;
        err = EdsSetPropertyData(camera_, kEdsPropID_Record, 0, sizeof(recordStart), &recordStart);
        if (err == EDS_ERR_OK) {
            recording_ = true;
        } else {
            printError("start recording", err);
        }
        return err;
    }

    EdsError stopRecording() {
        EdsError err = initialize();
        if (err != EDS_ERR_OK) {
            return err;
        }

        EdsUInt32 recordStop = 0;
        err = EdsSetPropertyData(camera_, kEdsPropID_Record, 0, sizeof(recordStop), &recordStop);
        if (err != EDS_ERR_OK) {
            printError("stop recording", err);
        }
        // The intent of "stop" is "not recording": clear the flag even when the
        // EDSDK call errors because the camera had already self-stopped (e.g. it
        // hit the 4 GB file / 29-minute movie limit). Otherwise a self-stop
        // leaves recording_ stuck true and wedges every subsequent run.
        recording_ = false;
        return err;
    }

    bool isRecording() const {
        return recording_;
    }

    void shutdown() {
        if (sessionOpen_ && camera_ != nullptr) {
            EdsCloseSession(camera_);
            sessionOpen_ = false;
        }

        if (camera_ != nullptr) {
            EdsRelease(camera_);
            camera_ = nullptr;
        }

        if (sdkInitialized_) {
            EdsTerminateSDK();
            sdkInitialized_ = false;
        }
    }

private:
    EdsError enableMovieMode() {
        if (camera_ == nullptr) {
            return EDS_ERR_DEVICE_NOT_FOUND;
        }

        EdsUInt32 movieMode = 0;
        EdsError err = EdsGetPropertyData(
            camera_, kEdsPropID_FixedMovie, 0, sizeof(movieMode), &movieMode
        );

        if (err == EDS_ERR_OK && movieMode == 0) {
            err = EdsSendCommand(camera_, kEdsCameraCommand_MovieSelectSwON, 0);
            if (err == EDS_ERR_OK) {
                std::this_thread::sleep_for(std::chrono::milliseconds(300));
            }
        }

        return err;
    }

    bool sdkInitialized_ = false;
    bool sessionOpen_ = false;
    bool recording_ = false;
    EdsCameraRef camera_ = nullptr;
};

int runDetect() {
    EdsError err = EdsInitializeSDK();
    if (err != EDS_ERR_OK) {
        printError("EdsInitializeSDK", err);
        return 1;
    }

    EdsCameraListRef cameraList = nullptr;
    EdsUInt32 count = 0;
    err = EdsGetCameraList(&cameraList);
    if (err == EDS_ERR_OK) {
        err = EdsGetChildCount(cameraList, &count);
    }
    if (cameraList != nullptr) {
        EdsRelease(cameraList);
    }
    EdsTerminateSDK();

    if (err != EDS_ERR_OK) {
        printError("detect camera", err);
        return 1;
    }

    std::cout << "OK cameras=" << count << std::endl;
    return count > 0 ? 0 : 2;
}

int runDaemon() {
    CameraControl control;
    EdsError err = control.initialize();
    if (err != EDS_ERR_OK) {
        std::cout << "ERR ready " << errorToHex(err) << std::endl;
        return 1;
    }

    std::cout << "OK ready" << std::endl;
    std::cout.flush();

    std::string command;
    while (std::getline(std::cin, command)) {
        auto commandReceivedEpochUs = epochMicroseconds();
        auto commandStarted = std::chrono::steady_clock::now();

        if (command == "start") {
            err = control.startRecording();
        } else if (command == "stop") {
            err = control.stopRecording();
        } else if (command == "status") {
            std::cout << "OK recording=" << (control.isRecording() ? 1 : 0) << std::endl;
            std::cout.flush();
            continue;
        } else if (command == "quit") {
            std::cout << "OK bye" << std::endl;
            std::cout.flush();
            break;
        } else {
            std::cout << "ERR unknown_command" << std::endl;
            std::cout.flush();
            continue;
        }

        auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - commandStarted
        );
        auto commandCompletedEpochUs = epochMicroseconds();

        if (err == EDS_ERR_OK) {
            std::cout << "OK " << command
                      << " elapsed_us=" << elapsed.count()
                      << " daemon_received_epoch_us=" << commandReceivedEpochUs
                      << " daemon_completed_epoch_us=" << commandCompletedEpochUs
                      << std::endl;
        } else {
            std::cout << "ERR " << command << " " << errorToHex(err)
                      << " elapsed_us=" << elapsed.count()
                      << " daemon_received_epoch_us=" << commandReceivedEpochUs
                      << " daemon_completed_epoch_us=" << commandCompletedEpochUs
                      << std::endl;
        }
        std::cout.flush();
    }

    return 0;
}

int runOneShot(const std::string& command) {
    CameraControl control;
    EdsError err = EDS_ERR_OK;

    if (command == "start") {
        err = control.startRecording();
    } else if (command == "stop") {
        err = control.stopRecording();
    } else {
        std::cerr << "Usage: CameraControl detect|daemon|start|stop" << std::endl;
        return 64;
    }

    if (err == EDS_ERR_OK) {
        std::cout << "OK " << command << std::endl;
        return 0;
    }

    std::cout << "ERR " << command << " " << errorToHex(err) << std::endl;
    return 1;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "Usage: CameraControl detect|daemon|start|stop" << std::endl;
        return 64;
    }

    std::string command = argv[1];
    if (command == "detect") {
        return runDetect();
    }
    if (command == "daemon") {
        return runDaemon();
    }
    return runOneShot(command);
}
