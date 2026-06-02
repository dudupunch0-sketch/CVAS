#include "bpc_project_types.hpp"

BpcProjectBaseProcessor::BpcProjectBaseProcessor() {
}

BpcProjectBaseProcessor::~BpcProjectBaseProcessor() {
}

int BpcProjectBaseProcessor::scale_value(int value) {
    static int call_count = 0;
    call_count += 1;
    return value + call_count;
}

BpcProjectDerivedProcessor::BpcProjectDerivedProcessor(const std::string& name) : name_(name) {
}

BpcProjectDerivedProcessor::~BpcProjectDerivedProcessor() {
}

int BpcProjectDerivedProcessor::process(int value, const int &readonly_ref) {
    int mutable_value = value;
    return adjust(mutable_value, readonly_ref, name_);
}

const char *BpcProjectDerivedProcessor::label() const {
    return name_.c_str();
}

int BpcProjectDerivedProcessor::adjust(
    int &mutable_ref,
    const int &readonly_ref,
    const std::string& tag
) {
    static int call_count = 0;
    call_count += 1;
    mutable_ref += readonly_ref;
    if (tag.size() > 0) {
        mutable_ref += (int)tag.size();
    }
    return BpcProjectBaseProcessor::scale_value(mutable_ref + call_count);
}

const char *project_select_processor_label(const BpcProjectBaseProcessor *processor) {
    if (processor == 0) {
        return "none";
    }
    return processor->label();
}
